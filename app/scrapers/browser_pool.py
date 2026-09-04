"""One shared Chromium instance for the process; each scrape gets its own fresh
BrowserContext (isolated cookies/storage, randomized UA/viewport) so requests
don't leak state between clients or sites. Concurrency is capped with a
semaphore since headless browser contexts are memory/CPU heavy — extra
requests wait their turn instead of spawning unbounded contexts.
"""

import asyncio
import logging
import random

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright
from playwright_stealth import Stealth

from app.config import settings
from app.scrapers import shopee_login
from app.scrapers.proxy_provider import get_proxy_provider

logger = logging.getLogger(__name__)

# Built once and reused: Stealth() precomputes its init-script payload from
# the evasion flags, so every context should share one instance rather than
# rebuilding it per request.
_stealth = Stealth()

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
]

_playwright: Playwright | None = None
_browser: Browser | None = None
_semaphore: asyncio.Semaphore | None = None


async def startup() -> None:
    global _playwright, _browser, _semaphore
    _playwright = await async_playwright().start()
    if settings.browser_mode == "brightdata_cdp":
        _browser = await _playwright.chromium.connect_over_cdp(settings.brightdata_ws_endpoint)
    else:
        _browser = await _playwright.chromium.launch(headless=settings.playwright_headless)
    _semaphore = asyncio.Semaphore(settings.max_concurrent_scrapes)

    if settings.shopee_login_enabled:
        try:
            await shopee_login.login_and_cache_session(_browser)
        except Exception:
            logger.warning("Shopee login failed; continuing with anonymous scraping", exc_info=True)


async def shutdown() -> None:
    if _browser:
        await _browser.close()
    if _playwright:
        await _playwright.stop()


class ManagedContext:
    """Async context manager: acquires a concurrency slot and yields a fresh, isolated BrowserContext."""

    def __init__(self, locale: str, timezone_id: str, geolocation: dict | None, country: str = "") -> None:
        self._locale = locale
        self._timezone_id = timezone_id
        self._geolocation = geolocation
        self._country = country
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> BrowserContext:
        assert _semaphore is not None and _browser is not None, "browser_pool.startup() not called"
        await _semaphore.acquire()

        try:
            storage_state = shopee_login.get_cached_storage_state()
            if settings.browser_mode == "brightdata_cdp":
                # Bright Data's Scraping Browser handles proxy/exit-IP rotation
                # and fingerprinting on its own side; local UA/proxy/stealth
                # overrides here would just fight its own patches.
                self._context = await _browser.new_context(locale=self._locale, storage_state=storage_state)
            else:
                proxy = get_proxy_provider().next_proxy(country=self._country)
                self._context = await _browser.new_context(
                    user_agent=random.choice(_USER_AGENTS),
                    viewport={"width": 1366, "height": 768},
                    locale=self._locale,
                    timezone_id=self._timezone_id,
                    geolocation=self._geolocation,
                    permissions=["geolocation"] if self._geolocation else [],
                    proxy=proxy,
                    storage_state=storage_state,
                    # Bright Data's Web Unlocker (and similar unblocking
                    # proxies) MITM the TLS connection to inspect/unblock
                    # responses, so it presents its own certificate instead of
                    # the target site's — Chromium rejects that by default.
                    ignore_https_errors=settings.proxy_mode == "brightdata_unlocker",
                )
                await _stealth.apply_stealth_async(self._context)
            return self._context
        except Exception:
            _semaphore.release()
            raise

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._context:
            await self._context.close()
        _semaphore.release()


def acquire_context(
    locale: str = "en-US", timezone_id: str = "UTC", geolocation: dict | None = None, country: str = ""
) -> ManagedContext:
    return ManagedContext(locale, timezone_id, geolocation, country)
