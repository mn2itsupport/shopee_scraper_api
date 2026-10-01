"""Reads a live Shopee pdp/get_pc response from a real, human-logged-in Chrome
attached over CDP (started via scripts/launch_real_chrome_th.ps1). Used by
scripts/price_agent.py (which runs next to that Chrome); the API itself never
touches CDP directly when deployed — it calls the agent over HTTP instead.
"""

import asyncio
import logging

from playwright.async_api import async_playwright

from app.scrapers.sites._shopee_common import _normalize_shopee_url

logger = logging.getLogger(__name__)


async def capture_get_pc(cdp_url: str, url: str, timeout_s: int) -> dict:
    """Opens one tab in the attached Chrome's default context (so it carries
    the real login), waits for the page's own get_pc XHR, and returns its
    JSON body. Closes only that tab. Raises on connect/navigation/timeout.
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

    return await asyncio.wait_for(_capture(), timeout=timeout_s + 10)
