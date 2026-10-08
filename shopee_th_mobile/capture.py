"""Reads Shopee TH's mobile-web PDP response (pdp/get_rw) from a dedicated,
human-logged-in Chrome that always presents as a phone (started via
shopee_th_mobile/launch_chrome.ps1, CDP on 127.0.0.1:9223).

Standalone: shares nothing with app/ or scripts/price_agent.py — its own
Chrome profile, port, settings and lock — so it can't disturb the desktop
get_pc agent that serves production.
"""

import asyncio
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

CDP_URL = os.getenv("SHOPEE_TH_MOBILE_CDP_URL", "http://127.0.0.1:9223")
TIMEOUT_SECONDS = int(os.getenv("SHOPEE_TH_MOBILE_TIMEOUT_SECONDS", "45"))
# Minimum gap between two product loads, so the account never sees a burst.
MIN_INTERVAL_SECONDS = float(os.getenv("SHOPEE_TH_MOBILE_MIN_INTERVAL_SECONDS", "20"))
# After Shopee's risk control blocks a load, refuse all loads for this long
# instead of retrying into it — repeated blocked loads get an account flagged.
BLOCK_COOLDOWN_SECONDS = int(os.getenv("SHOPEE_TH_MOBILE_BLOCK_COOLDOWN_SECONDS", "1800"))

# Must match launch_chrome.ps1's --user-agent, so tabs opened by hand (login)
# and tabs opened by capture() look like the same phone.
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36"
)
_UA_METADATA = {
    "brands": [{"brand": "Google Chrome", "version": "129"}, {"brand": "Chromium", "version": "129"},
               {"brand": "Not=A?Brand", "version": "24"}],
    "platform": "Android", "platformVersion": "14.0.0", "architecture": "", "model": "Pixel 8", "mobile": True,
}

# Shopee's risk-control rejection, seen live 2026-10-08 on pdp/get_rw.
RISK_CONTROL_ERROR = 90309999

_PRODUCT_I = re.compile(r"-i\.(\d+)\.(\d+)")
_PRODUCT_PATH = re.compile(r"/product/(\d+)/(\d+)")


class BlockedError(RuntimeError):
    """Shopee answered with its traffic-verification wall / risk-control code."""


class CoolingDownError(RuntimeError):
    """A recent block put the capturer in cooldown; nothing was loaded."""


def product_url(url: str) -> str:
    """Any shopee.co.th product link (…-i.SHOP.ITEM, /product/SHOP/ITEM, a
    pdp/get_pc or get_rw API URL, with or without https://) → the canonical
    product page."""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url.lstrip("/")
    match = _PRODUCT_I.search(url) or _PRODUCT_PATH.search(url)
    if match:
        shop_id, item_id = match.groups()
    else:
        query = parse_qs(urlsplit(url).query)
        shop_id, item_id = query.get("shop_id", [""])[0], query.get("item_id", [""])[0]
    if not (shop_id.isdigit() and item_id.isdigit()):
        raise ValueError(f"not a shopee.co.th product URL: {url}")
    return f"https://shopee.co.th/product-i.{shop_id}.{item_id}"


_lock = asyncio.Lock()
_last_load = 0.0
_blocked_until = 0.0


def cooldown_remaining() -> int:
    return max(0, int(_blocked_until - time.monotonic()))


async def capture_get_rw(url: str) -> tuple[str, dict]:
    """Loads one product as the phone and returns (endpoint, body) where
    endpoint is "get_rw" (or "get_pc" if Shopee served the desktop bundle
    anyway) and body is that response's JSON. One tab at a time, spaced by
    MIN_INTERVAL_SECONDS. Raises BlockedError on Shopee's risk-control wall,
    CoolingDownError while recovering from one."""
    global _last_load, _blocked_until
    page_url = product_url(url)
    async with _lock:
        if cooldown_remaining():
            raise CoolingDownError(f"blocked recently, cooling down {cooldown_remaining()}s more")
        wait = MIN_INTERVAL_SECONDS - (time.monotonic() - _last_load)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_load = time.monotonic()
        try:
            return await asyncio.wait_for(_capture(page_url), timeout=TIMEOUT_SECONDS + 10)
        except BlockedError:
            _blocked_until = time.monotonic() + BLOCK_COOLDOWN_SECONDS
            raise


async def _capture(page_url: str) -> tuple[str, dict]:
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(CDP_URL)
        page = None
        try:
            page = await browser.contexts[0].new_page()
            cdp = await page.context.new_cdp_session(page)
            await cdp.send("Emulation.setUserAgentOverride",
                           {"userAgent": MOBILE_UA, "platform": "Android", "userAgentMetadata": _UA_METADATA})
            await cdp.send("Emulation.setDeviceMetricsOverride",
                           {"width": 412, "height": 915, "deviceScaleFactor": 2.625, "mobile": True})
            await cdp.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})

            is_pdp = lambda r: "/api/v4/pdp/get_rw" in r.url or "/api/v4/pdp/get_pc" in r.url  # noqa: E731
            async with page.expect_response(is_pdp, timeout=TIMEOUT_SECONDS * 1000) as info:
                await page.goto(page_url, wait_until="domcontentloaded", timeout=TIMEOUT_SECONDS * 1000)
            response = await info.value
            endpoint = "get_rw" if "/pdp/get_rw" in response.url else "get_pc"
            try:
                body = await response.json()
            except Exception:
                # Same CDP body-buffer flake real_chrome.py works around.
                body = await page.evaluate(
                    "async (u) => (await fetch(u, {credentials: 'include'})).json()", response.url
                )
            if "/verify/" in page.url or (isinstance(body, dict) and body.get("error") == RISK_CONTROL_ERROR):
                raise BlockedError(f"Shopee risk control blocked {page_url} (page ended on {page.url})")
            return endpoint, body
        finally:
            if page is not None:
                await page.close()
            await browser.close()
