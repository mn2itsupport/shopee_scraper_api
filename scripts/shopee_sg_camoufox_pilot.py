"""One-off pilot: try camoufox (a hardened, fingerprint-spoofed Firefox build
— a different browser engine entirely from everything else tried against
Shopee so far: bare Playwright, Patchright, nodriver, Bright Data's Scraping
Browser are all Chromium-based) against a real shopee.sg PDP, to see whether
it clears the risk-control wall that's blocked every Chromium-based
browser-driven transport tried so far (see shopee_th.py's comments and
scripts/shopee_th_nodriver_pilot.py's result).

Standalone and read-only: does not import/modify any existing scraper code
(only reuses ProxyProvider to build the same Bright Data residential-SG
proxy every other transport already targets via unlocker_country).

Run from shopee_scraper_api/:

    python scripts/shopee_sg_camoufox_pilot.py [product_url]
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from camoufox.async_api import AsyncCamoufox  # noqa: E402
from playwright.async_api import Route  # noqa: E402

from app.scrapers.captcha import is_captcha_html  # noqa: E402
from app.scrapers.proxy_provider import get_proxy_provider  # noqa: E402

DEFAULT_URL = "https://shopee.sg/product-i.10208.14298826796"
_PDP_API_FRAGMENTS = ["pdp/get_pc", "/item/get"]
_WAIT_SECONDS = 45
OUT_PATH = Path(__file__).resolve().parent.parent / "logs" / "shopee_sg_camoufox_pilot_result.json"


def _playwright_proxy(country: str) -> dict | None:
    proxy = get_proxy_provider().next_proxy(country=country)
    if proxy is None:
        return None
    return {"server": proxy["server"], "username": proxy.get("username", ""), "password": proxy.get("password", "")}


async def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    proxy = _playwright_proxy("sg")
    print(f"Using proxy: {'yes (Bright Data, country=sg)' if proxy else 'no'}")

    captured: dict = {}

    async def handle_pdp_route(route: Route) -> None:
        if "task" in captured:
            await route.continue_()
            return
        api_response = await route.fetch()
        captured["task"] = asyncio.ensure_future(api_response.json())
        await route.fulfill(response=api_response)

    async with AsyncCamoufox(headless=True, proxy=proxy, geoip=True, humanize=True) as browser:
        context = await browser.new_context()
        page = await context.new_page()
        for fragment in _PDP_API_FRAGMENTS:
            await page.route(f"**/*{fragment}*", handle_pdp_route)

        print("Warming up: shopee.sg home page...")
        try:
            await page.goto("https://shopee.sg", timeout=60000, wait_until="domcontentloaded")
            await asyncio.sleep(2)
        except Exception as exc:  # noqa: BLE001 - best-effort warm-up
            print(f"  (warm-up navigation failed, continuing anyway: {exc})")

        print(f"Navigating: {url}")
        try:
            await page.goto(url, timeout=60000, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001 - want the raw failure for inspection
            print(f"  goto failed: {exc}")

        for _ in range(int(_WAIT_SECONDS / 0.5)):
            if "task" in captured:
                break
            await asyncio.sleep(0.5)

        html = await page.content()
        captcha_detected = is_captcha_html(html)

        api_body = None
        api_body_error = None
        if "task" in captured:
            try:
                api_body = await captured["task"]
            except Exception as exc:  # noqa: BLE001
                api_body_error = f"{type(exc).__name__}: {exc}"

        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(OUT_PATH.with_suffix(".png")))

        result = {
            "product_url_requested": url,
            "final_page_url": page.url,
            "captcha_signature_detected": captcha_detected,
            "pdp_api_call_observed": "task" in captured,
            "pdp_api_body_top_level_keys": list(api_body.keys()) if isinstance(api_body, dict) else None,
            "pdp_api_body_error_code": (api_body or {}).get("error") if isinstance(api_body, dict) else None,
            "pdp_api_body_fetch_error": api_body_error,
            "pdp_api_body": api_body,
            "html_len": len(html),
        }
        await context.close()

    OUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote result to {OUT_PATH}")
    print(f"Screenshot: {OUT_PATH.with_suffix('.png')}")
    print(f"Final page URL: {result['final_page_url']}")
    print(f"CAPTCHA signature detected: {result['captcha_signature_detected']}")
    print(f"pdp/get_pc call observed: {result['pdp_api_call_observed']}")
    print(f"pdp/get_pc body top-level keys: {result['pdp_api_body_top_level_keys']}")
    print(f"pdp/get_pc body error code (if any): {result['pdp_api_body_error_code']}")


if __name__ == "__main__":
    asyncio.run(main())
