"""Reads a live Shopee pdp/get_pc response from a real, human-logged-in Chrome
attached over CDP (started via scripts/launch_real_chrome_th.ps1). Used by
scripts/price_agent.py (which runs next to that Chrome); the API itself never
touches CDP directly when deployed — it calls the agent over HTTP instead.
"""

import asyncio
import logging
from urllib.parse import urlsplit, urlunsplit

from playwright.async_api import async_playwright

from app.scrapers.sites._shopee_common import _normalize_shopee_url

logger = logging.getLogger(__name__)

# One tab at a time, same as scripts/price_agent.py: a real logged-in session
# (or a GoLogin cloud profile, which can't run twice concurrently) shouldn't
# see a burst of parallel automated navigations. Only matters when the API
# attaches directly via SHOPEE_TH_REAL_CHROME_CDP_URL instead of the agent.
_lock = asyncio.Semaphore(1)


def _redact(cdp_url: str) -> str:
    """cdp_url may carry a secret in its query string (GoLogin cloud:
    wss://cloudbrowser.gologin.com/connect?token=...&profile=...) and
    Playwright echoes the full URL in its connect errors."""
    parts = urlsplit(cdp_url)
    return urlunsplit(parts._replace(query="<redacted>")) if parts.query else cdp_url


async def capture_get_pc(cdp_url: str, url: str, timeout_s: int) -> dict:
    """Opens one tab in the attached Chrome's default context (so it carries
    the real login), waits for the page's own get_pc XHR, and returns its
    JSON body. Closes only that tab. Raises on connect/navigation/timeout,
    with cdp_url's query string (which may hold a token) redacted from the
    error message.
    """

    async def _capture() -> dict:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(cdp_url)
            page = None
            try:
                page = await browser.contexts[0].new_page()
                async with page.expect_response(lambda r: "pdp/get_pc" in r.url, timeout=timeout_s * 1000) as info:
                    await page.goto(_normalize_shopee_url(url), wait_until="domcontentloaded", timeout=timeout_s * 1000)
                response = await info.value
                try:
                    return await response.json()
                except Exception:
                    # Live-buffered body reads can intermittently fail over
                    # CDP ("No data found for resource") — re-fetch the same
                    # URL from inside the page, which carries the same
                    # session and Shopee's own request handling.
                    return await page.evaluate(
                        "async (u) => (await fetch(u, {credentials: 'include'})).json()", response.url
                    )
            finally:
                if page is not None:
                    await page.close()
                await browser.close()

    async with _lock:
        try:
            return await asyncio.wait_for(_capture(), timeout=timeout_s + 10)
        except Exception as exc:
            message = str(exc)
            if cdp_url in message:
                # `from None`: a chained traceback would print the original,
                # unredacted message right back into the logs.
                raise RuntimeError(
                    f"{type(exc).__name__}: {message.replace(cdp_url, _redact(cdp_url))}"
                ) from None
            raise
