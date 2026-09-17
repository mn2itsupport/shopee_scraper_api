"""One shared Chromium instance for the process; each scrape gets its own fresh
BrowserContext (isolated cookies/storage, randomized UA/viewport) so requests
don't leak state between clients or sites. Concurrency is capped with a
semaphore since headless browser contexts are memory/CPU heavy — extra
requests wait their turn instead of spawning unbounded contexts.
"""

import asyncio
import logging
import os
import random
import subprocess
import sys
from pathlib import Path

from patchright.async_api import Playwright as PatchrightDriver
from patchright.async_api import async_playwright as async_patchright
from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright
from playwright_stealth import Stealth

from app.config import settings
from app.scrapers import http_pool, shopee_login
from app.scrapers.base import accept_language_for
from app.scrapers.proxy_provider import get_proxy_provider
from app.scrapers.sites.shopee_br import ShopeeBRScraper
from app.scrapers.sites.shopee_th import ShopeeTHScraper

# On-disk profile for shopee_th's persistent context (see _shopee_th_context
# below) — cookies/localStorage here survive app restarts, unlike every other
# site's fresh-per-request context.
_SHOPEE_TH_PROFILE_DIR = Path("browser_profiles") / "shopee_th"
_SHOPEE_TH_LOGIN_URL = "https://shopee.co.th/buyer/login"

# Same idea as shopee_th above, added after shopee_th's own persistent
# profile (real human login) still got CaptchaBlockedError on every scrape
# attempt against shopee_br via the plain browser/CDP transports (confirmed
# live 2026-09-10 — see .env's SHOPEE_BR_BROWSER_MODE_OVERRIDE comment).
# Only active once SHOPEE_BR_USE_PERSISTENT_PROFILE=true — off by default
# until scripts/shopee_br_manual_login.py has actually seeded it.
_SHOPEE_BR_PROFILE_DIR = Path("browser_profiles") / "shopee_br"
_SHOPEE_BR_LOGIN_URL = "https://shopee.com.br/buyer/login"

logger = logging.getLogger(__name__)

# Built once and reused: Stealth() precomputes its init-script payload from
# the evasion flags, so every context should share one instance rather than
# rebuilding it per request.
_stealth = Stealth()


def _harden_profile_dir_permissions(path: Path) -> None:
    """Restrict a persistent browser profile directory (_SHOPEE_TH_PROFILE_DIR /
    _SHOPEE_BR_PROFILE_DIR) to the current OS user only. It holds live,
    already-authenticated Shopee session cookies — equivalent in sensitivity
    to a credential — but by default (at least on Windows, confirmed via
    `icacls`) a freshly-created directory inherits access for every
    authenticated user on the machine, not just whoever runs this process.
    Called on every startup (idempotent) rather than only right after
    mkdir(), so an already-existing directory created before this existed
    gets locked down too. Best-effort: failing here logs a warning but never
    blocks startup — worst case is the same permissions the directory would
    have had anyway.
    """
    try:
        if sys.platform == "win32":
            username = os.environ.get("USERNAME", "")
            grants = ["SYSTEM:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"]  # SYSTEM, BUILTIN\Administrators
            if username:
                grants.append(f"{username}:(OI)(CI)F")
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", *grants],
                check=True,
                capture_output=True,
            )
        else:
            path.chmod(0o700)
    except Exception:
        logger.warning("Could not harden permissions on profile directory %s", path, exc_info=True)

class _BrowserProfile:
    """One internally-consistent desktop-Chrome identity: every field must
    agree with every other, because that's exactly what anti-bot vendors
    (DataDome/Akamai-style) diff against each other. Picking a User-Agent
    string alone (the old _USER_AGENTS list) is not enough — Chromium
    generates the sec-ch-ua* Client Hints headers itself from the real
    bundled engine version and the real host OS, ignoring whatever
    User-Agent override new_context() is given. So a spoofed UA claiming
    Chrome/128 on macOS while the actual sec-ch-ua* headers say Chrome/131
    on Windows is a much stronger tell than sending no UA override at all.
    Forcing all four fields as one matched bundle via extra_http_headers
    (below) closes that gap instead of leaving it to chance.
    """

    def __init__(self, user_agent: str, sec_ch_ua: str, sec_ch_ua_platform: str) -> None:
        self.user_agent = user_agent
        self.sec_ch_ua = sec_ch_ua
        self.sec_ch_ua_platform = sec_ch_ua_platform


# Chrome major version kept current with whatever playwright==<pinned version
# in requirements.txt> actually bundles — check `python -c "import asyncio,
# playwright.async_api as p; ..."` (or just the failure mode: a stale major
# version here is a UA-vs-real-Chrome-release-date mismatch, a softer but
# still checkable signal) and refresh this list when bumping playwright.
_BROWSER_PROFILES = [
    _BrowserProfile(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        sec_ch_ua='"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
        sec_ch_ua_platform='"Windows"',
    ),
    _BrowserProfile(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        sec_ch_ua='"Google Chrome";v="130", "Chromium";v="130", "Not_A Brand";v="99"',
        sec_ch_ua_platform='"Windows"',
    ),
    _BrowserProfile(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        sec_ch_ua='"Not_A Brand";v="24", "Chromium";v="131", "Google Chrome";v="131"',
        sec_ch_ua_platform='"macOS"',
    ),
    _BrowserProfile(
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        sec_ch_ua='"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
        sec_ch_ua_platform='"Linux"',
    ),
]


def _make_accept_language_router(locale: str):
    """context.new_context(locale=...) regenerates a bare, unweighted
    Accept-Language on every request at a lower level than extra_http_headers
    (confirmed live: passing "Accept-Language" via extra_http_headers, or via
    a later set_extra_http_headers() call, both get silently overwritten back
    to the bare locale tag) — routing every request through this handler and
    rewriting the header on its way out is the one thing that actually sticks
    on the wire (confirmed against a real echo server).
    """
    accept_language = accept_language_for(locale)

    async def _router(route) -> None:
        headers = dict(route.request.headers)
        headers["accept-language"] = accept_language
        await route.continue_(headers=headers)

    return _router

_playwright: Playwright | None = None
_browser: Browser | None = None
_semaphore: asyncio.Semaphore | None = None
# shopee_th only (see BaseScraper.browser_mode_override on ShopeeTHScraper):
# one BrowserContext launched once from _SHOPEE_TH_PROFILE_DIR and kept open
# for the process lifetime, reused by every shopee_th scrape (as separate
# pages within it) instead of a fresh context per request. Every automated
# shopee_th login attempt (local Chromium, Bright Data's Scraping Browser)
# got blocked by Shopee's traffic-verification wall before completing, so
# this profile is meant to be seeded by a real human login instead — run
# scripts/shopee_th_manual_login.py once, which opens a real, visible browser
# window against this same profile directory for you to log into by hand.
_shopee_th_context: BrowserContext | None = None
# Patchright is a separate installation from the shared Playwright driver
# above (_playwright) — it needs its own driver instance, started/stopped
# alongside _shopee_th_context specifically.
_shopee_th_patchright_driver: PatchrightDriver | None = None
# None = never confirmed either way yet; True/False = last check's result —
# compared against on every subsequent check so an alert only fires on an
# actual valid<->invalid transition, not on every periodic tick.
_shopee_th_session_last_known_valid: bool | None = None
_shopee_th_session_watchdog_task: asyncio.Task | None = None

# shopee_br mirror of the shopee_th persistent-profile state above — see
# _SHOPEE_BR_PROFILE_DIR's comment for why this exists.
_shopee_br_context: BrowserContext | None = None
_shopee_br_patchright_driver: PatchrightDriver | None = None
_shopee_br_session_last_known_valid: bool | None = None
_shopee_br_session_watchdog_task: asyncio.Task | None = None

# Watches shopee_login._cached_storage_state instead of a persistent-profile
# context — only started (in startup(), below) for a site whose login is
# enabled but whose persistent profile is NOT in use, since ManagedContext
# only ever reads the cached storage_state in that case (a site with an
# active persistent context is already covered by the watchdogs above).
# Keyed by site_key rather than one pair of globals per site, since
# shopee_login.enabled_configs() can return an arbitrary set of sites.
_cached_session_last_known_valid: dict[str, bool | None] = {}
_cached_session_watchdog_tasks: dict[str, asyncio.Task] = {}


async def _alert_session(site_key: str, webhook_url: str, message: str) -> None:
    logger.warning("%s session alert: %s", site_key, message)
    if not webhook_url:
        return
    try:
        await http_pool.get_client().post(webhook_url, json={"text": f"[shopee_scraper_api] {message}"})
    except Exception:
        logger.warning("Failed to POST %s session alert to configured webhook", site_key, exc_info=True)


async def _check_persistent_profile_session(
    site_key: str,
    context: BrowserContext | None,
    login_url: str,
    webhook_url: str,
    manual_login_script: str,
    last_known_valid: bool | None,
    reason: str,
) -> bool | None:
    """Runs shopee_login.check_persistent_profile_session_valid, logs the
    result, and fires _alert_session only when the state actually changed
    since the last check (startup counts as the first check) — called both
    once at startup and on every per-site session watchdog tick. Returns the
    session_valid result so the caller can update its own last-known-valid
    global (kept per-site by the caller rather than here, since this function
    has no per-site global to mutate itself).
    """
    if context is None:
        return last_known_valid

    session_valid = await shopee_login.check_persistent_profile_session_valid(context, login_url)
    if session_valid is True:
        logger.info("%s session check (%s): valid", site_key, reason)
    elif session_valid is False:
        logger.warning(
            "%s session check (%s): no longer valid (still redirected to the login form) — "
            "run %s again to refresh it",
            site_key,
            reason,
            manual_login_script,
        )
    else:
        logger.warning(
            "%s session check (%s): inconclusive — the check itself was caught by Shopee's "
            "traffic-verification wall (not necessarily a dead session)",
            site_key,
            reason,
        )

    if session_valid is False and last_known_valid is not False:
        await _alert_session(site_key, webhook_url, f"session is no longer valid — run {manual_login_script} to refresh it")
    elif session_valid is True and last_known_valid is False:
        await _alert_session(site_key, webhook_url, "session has recovered and is valid again")

    return session_valid if session_valid is not None else last_known_valid


async def _check_shopee_th_session(reason: str) -> None:
    global _shopee_th_session_last_known_valid
    _shopee_th_session_last_known_valid = await _check_persistent_profile_session(
        "shopee_th",
        _shopee_th_context,
        _SHOPEE_TH_LOGIN_URL,
        settings.shopee_th_session_alert_webhook_url,
        "scripts/shopee_th_manual_login.py",
        _shopee_th_session_last_known_valid,
        reason,
    )


async def _shopee_th_session_watchdog() -> None:
    interval_seconds = settings.shopee_th_session_check_interval_minutes * 60
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await _check_shopee_th_session("periodic")
        except Exception:
            logger.warning("shopee_th periodic session check itself failed", exc_info=True)


async def _check_shopee_br_session(reason: str) -> None:
    global _shopee_br_session_last_known_valid
    _shopee_br_session_last_known_valid = await _check_persistent_profile_session(
        "shopee_br",
        _shopee_br_context,
        _SHOPEE_BR_LOGIN_URL,
        settings.shopee_br_session_alert_webhook_url,
        "scripts/shopee_br_manual_login.py",
        _shopee_br_session_last_known_valid,
        reason,
    )


async def _shopee_br_session_watchdog() -> None:
    interval_seconds = settings.shopee_br_session_check_interval_minutes * 60
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await _check_shopee_br_session("periodic")
        except Exception:
            logger.warning("shopee_br periodic session check itself failed", exc_info=True)


async def _check_cached_session(login_config: shopee_login.SiteLoginConfig, webhook_url: str, reason: str) -> None:
    site_key = login_config.site_key
    last_known_valid = _cached_session_last_known_valid.get(site_key)
    session_valid = await shopee_login.check_cached_session_valid(_browser, login_config)

    if session_valid is True:
        logger.info("%s cached-session check (%s): valid", site_key, reason)
    elif session_valid is False:
        logger.warning(
            "%s cached-session check (%s): no longer valid — restart the app to log in again "
            "(login_and_cache_session reruns at startup)",
            site_key,
            reason,
        )
    else:
        logger.warning(
            "%s cached-session check (%s): inconclusive — the check itself was caught by Shopee's "
            "traffic-verification wall (not necessarily a dead session)",
            site_key,
            reason,
        )

    if session_valid is False and last_known_valid is not False:
        await _alert_session(site_key, webhook_url, "cached login session is no longer valid — restart the app to log in again")
    elif session_valid is True and last_known_valid is False:
        await _alert_session(site_key, webhook_url, "cached login session has recovered and is valid again")

    _cached_session_last_known_valid[site_key] = session_valid if session_valid is not None else last_known_valid


async def _cached_session_watchdog(
    login_config: shopee_login.SiteLoginConfig, webhook_url: str, interval_minutes: int
) -> None:
    interval_seconds = interval_minutes * 60
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await _check_cached_session(login_config, webhook_url, "periodic")
        except Exception:
            logger.warning("%s cached-session periodic check itself failed", login_config.site_key, exc_info=True)


async def startup() -> None:
    global _playwright, _browser, _semaphore, _shopee_th_context, _shopee_th_session_last_known_valid
    global _shopee_th_session_watchdog_task, _shopee_th_patchright_driver
    global _shopee_br_context, _shopee_br_session_last_known_valid
    global _shopee_br_session_watchdog_task, _shopee_br_patchright_driver
    _playwright = await async_playwright().start()
    if settings.browser_mode == "brightdata_cdp":
        _browser = await _playwright.chromium.connect_over_cdp(settings.brightdata_ws_endpoint)
    else:
        _browser = await _playwright.chromium.launch(headless=settings.playwright_headless)
    _semaphore = asyncio.Semaphore(settings.max_concurrent_scrapes)

    if settings.shopee_th_use_persistent_profile:
        _SHOPEE_TH_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        _harden_profile_dir_permissions(_SHOPEE_TH_PROFILE_DIR)
        proxy = (
            get_proxy_provider().next_proxy(country=ShopeeTHScraper.unlocker_country)
            if settings.proxy_mode == "brightdata_residential"
            else None
        )
        # Patchright, not plain Playwright — it patches the CDP
        # Runtime.enable/Console.enable leaks and automation command-line
        # flags that playwright-stealth doesn't cover. Confirmed via live
        # testing that Shopee TH's anti-bot layer invalidates an
        # authenticated session on the very first plain-Playwright-driven
        # action after a real human login (any destination, same or fresh
        # process, matched proxy/IP/timezone/fingerprint — none of that
        # mattered), which points at CDP-level detection specifically. Needs
        # its own driver instance (_shopee_th_patchright_driver) — separate
        # installation from the shared Playwright driver above. No
        # Stealth() call here: Patchright replaces it rather than
        # complementing it, and layering stealth's JS injection on top risks
        # fighting Patchright's own patches.
        _shopee_th_patchright_driver = await async_patchright().start()
        _shopee_th_context = await _shopee_th_patchright_driver.chromium.launch_persistent_context(
            str(_SHOPEE_TH_PROFILE_DIR),
            headless=settings.playwright_headless,
            locale=ShopeeTHScraper.locale,
            timezone_id=ShopeeTHScraper.timezone_id,
            geolocation=ShopeeTHScraper.geolocation,
            permissions=["geolocation"] if ShopeeTHScraper.geolocation else [],
            viewport={"width": 1366, "height": 768},
            proxy=proxy,
            ignore_https_errors=settings.proxy_mode == "brightdata_unlocker",
        )
        existing_cookies = await _shopee_th_context.cookies()
        if not existing_cookies:
            logger.warning(
                "shopee_th persistent profile has no cookies yet — run "
                "scripts/shopee_th_manual_login.py to log in by hand before relying on this"
            )
            _shopee_th_session_last_known_valid = False
        else:
            # Cookies existing on disk doesn't mean the session is still
            # valid (it can expire/get revoked server-side) — actually check
            # rather than just trusting the count, so a dead session shows up
            # as a clear startup warning (and alert) instead of every later
            # scrape failing with no explanation of why.
            await _check_shopee_th_session("startup")

        # Keep re-checking for the rest of the process's life, not just once
        # at startup — a session can go stale hours into a long-running
        # process, well before the next scrape happens to hit it.
        _shopee_th_session_watchdog_task = asyncio.create_task(_shopee_th_session_watchdog())

    if settings.shopee_br_use_persistent_profile:
        # Mirrors the shopee_th block above exactly — see
        # _SHOPEE_BR_PROFILE_DIR's comment for why this exists for shopee_br
        # too. Own Patchright driver instance, same reasoning as shopee_th's:
        # a separate installation from both the shared Playwright driver and
        # shopee_th's own Patchright driver, since each launch_persistent_context
        # call needs a live driver for as long as its context stays open.
        _SHOPEE_BR_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        _harden_profile_dir_permissions(_SHOPEE_BR_PROFILE_DIR)
        proxy = (
            get_proxy_provider().next_proxy(country=ShopeeBRScraper.unlocker_country)
            if settings.proxy_mode == "brightdata_residential"
            else None
        )
        _shopee_br_patchright_driver = await async_patchright().start()
        _shopee_br_context = await _shopee_br_patchright_driver.chromium.launch_persistent_context(
            str(_SHOPEE_BR_PROFILE_DIR),
            headless=settings.playwright_headless,
            locale=ShopeeBRScraper.locale,
            timezone_id=ShopeeBRScraper.timezone_id,
            geolocation=ShopeeBRScraper.geolocation,
            permissions=["geolocation"] if ShopeeBRScraper.geolocation else [],
            viewport={"width": 1366, "height": 768},
            proxy=proxy,
            ignore_https_errors=settings.proxy_mode == "brightdata_unlocker",
        )
        existing_cookies = await _shopee_br_context.cookies()
        if not existing_cookies:
            logger.warning(
                "shopee_br persistent profile has no cookies yet — run "
                "scripts/shopee_br_manual_login.py to log in by hand before relying on this"
            )
            _shopee_br_session_last_known_valid = False
        else:
            await _check_shopee_br_session("startup")

        _shopee_br_session_watchdog_task = asyncio.create_task(_shopee_br_session_watchdog())

    for login_config in shopee_login.enabled_configs():
        # A config that opts into login_via_brightdata_cdp gets its own
        # short-lived Scraping Browser connection instead of reusing the
        # shared _browser — that way this one site's login can go through
        # Bright Data's anti-fingerprinting even while BROWSER_MODE (and the
        # shared local Chromium instance every other site relies on) stays
        # whatever the global setting is.
        login_browser = _browser
        cdp_browser = None
        if login_config.login_via_brightdata_cdp:
            try:
                cdp_browser = await _playwright.chromium.connect_over_cdp(settings.brightdata_ws_endpoint)
                login_browser = cdp_browser
            except Exception:
                logger.warning(
                    "Could not connect to Bright Data's Scraping Browser for %s login; skipping",
                    login_config.site_key,
                    exc_info=True,
                )
                continue

        try:
            await shopee_login.login_and_cache_session(login_browser, login_config)
        except Exception:
            logger.warning(
                "Shopee login failed for %s; continuing with anonymous scraping",
                login_config.site_key,
                exc_info=True,
            )
        finally:
            if cdp_browser is not None:
                await cdp_browser.close()

        uses_persistent_profile = (login_config.site_key == "shopee_th" and _shopee_th_context is not None) or (
            login_config.site_key == "shopee_br" and _shopee_br_context is not None
        )
        if uses_persistent_profile:
            # This site's scrapes actually read from the persistent-profile
            # context (see ManagedContext.__aenter__), not this cached
            # storage_state — already covered by the watchdog above, so
            # watching the cached copy too would just be a second, unused
            # session to track.
            continue

        webhook_url = (
            settings.shopee_th_session_alert_webhook_url
            if login_config.site_key == "shopee_th"
            else settings.shopee_br_session_alert_webhook_url
        )
        interval_minutes = (
            settings.shopee_th_session_check_interval_minutes
            if login_config.site_key == "shopee_th"
            else settings.shopee_br_session_check_interval_minutes
        )
        await _check_cached_session(login_config, webhook_url, "startup")
        _cached_session_watchdog_tasks[login_config.site_key] = asyncio.create_task(
            _cached_session_watchdog(login_config, webhook_url, interval_minutes)
        )


async def shutdown() -> None:
    for task in _cached_session_watchdog_tasks.values():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    if _shopee_th_session_watchdog_task is not None:
        _shopee_th_session_watchdog_task.cancel()
        try:
            await _shopee_th_session_watchdog_task
        except asyncio.CancelledError:
            pass
    if _shopee_th_context:
        await _shopee_th_context.close()
    if _shopee_th_patchright_driver:
        await _shopee_th_patchright_driver.stop()
    if _shopee_br_session_watchdog_task is not None:
        _shopee_br_session_watchdog_task.cancel()
        try:
            await _shopee_br_session_watchdog_task
        except asyncio.CancelledError:
            pass
    if _shopee_br_context:
        await _shopee_br_context.close()
    if _shopee_br_patchright_driver:
        await _shopee_br_patchright_driver.stop()
    if _browser:
        await _browser.close()
    if _playwright:
        await _playwright.stop()


class ManagedContext:
    """Async context manager: acquires a concurrency slot and yields a fresh, isolated BrowserContext."""

    def __init__(
        self, site_key: str, locale: str, timezone_id: str, geolocation: dict | None, country: str = ""
    ) -> None:
        self._site_key = site_key
        self._locale = locale
        self._timezone_id = timezone_id
        self._geolocation = geolocation
        self._country = country
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> BrowserContext:
        assert _semaphore is not None and _browser is not None, "browser_pool.startup() not called"
        await _semaphore.acquire()

        try:
            if self._site_key == "shopee_th" and _shopee_th_context is not None:
                # Reuse the one long-lived, persistent-profile context (see
                # startup()) instead of a fresh throwaway one — fetch_pdp()
                # opens its own page within it, same as with any other
                # context this yields. Not assigned to self._context, so
                # __aexit__ below correctly leaves it open afterward.
                return _shopee_th_context
            if self._site_key == "shopee_br" and _shopee_br_context is not None:
                return _shopee_br_context
            storage_state = shopee_login.get_cached_storage_state(self._site_key)
            if settings.browser_mode == "brightdata_cdp":
                # Bright Data's Scraping Browser handles proxy/exit-IP rotation
                # and fingerprinting on its own side; local UA/proxy/stealth
                # overrides here would just fight its own patches.
                self._context = await _browser.new_context(locale=self._locale, storage_state=storage_state)
            else:
                proxy = get_proxy_provider().next_proxy(country=self._country)
                profile = random.choice(_BROWSER_PROFILES)
                self._context = await _browser.new_context(
                    user_agent=profile.user_agent,
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
                    # Force sec-ch-ua*/platform instead of trusting
                    # Chromium's own defaults — they come from the real
                    # bundled engine + host OS regardless of the UA override
                    # above (see _BrowserProfile's docstring). extra_http_headers
                    # is enough for these three; Accept-Language is handled
                    # separately below (locale= silently wins over an
                    # Accept-Language passed here — confirmed live, see
                    # accept_language_for's docstring in base.py).
                    extra_http_headers={
                        "sec-ch-ua": profile.sec_ch_ua,
                        "sec-ch-ua-mobile": "?0",
                        "sec-ch-ua-platform": profile.sec_ch_ua_platform,
                    },
                )
                await _stealth.apply_stealth_async(self._context)
                await self._context.route("**/*", _make_accept_language_router(self._locale))
            return self._context
        except Exception:
            _semaphore.release()
            raise

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._context:
            await self._context.close()
        _semaphore.release()


def acquire_context(
    site_key: str,
    locale: str = "en-US",
    timezone_id: str = "UTC",
    geolocation: dict | None = None,
    country: str = "",
) -> ManagedContext:
    return ManagedContext(site_key, locale, timezone_id, geolocation, country)
