"""One-off pilot: try nodriver (async successor to undetected-chromedriver,
speaks its own custom DevTools implementation instead of standard CDP,
specifically to dodge the CDP-based automation fingerprints most anti-bot
vendors check for) against a real shopee.co.th PDP, to see whether it clears
the risk-control wall that's blocked every other browser-driven transport
tried against this site so far (bare Playwright, Patchright, Bright Data's
Scraping Browser, Bright Data's Web Unlocker REST API — see shopee_th.py's
comments).

Standalone and read-only: does not import/modify any existing scraper code
(only reuses ProxyProvider to build the same Bright Data residential-TH
proxy every other transport already targets via unlocker_country) — a real
Chrome install on this machine, driven by nodriver, routed through Bright
Data the same way _curl_cffi_fetch is.

Run from shopee_scraper_api/:

    python scripts/shopee_th_nodriver_pilot.py [product_url]
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nodriver  # noqa: E402
from nodriver.cdp import network  # noqa: E402

from app.scrapers.captcha import is_captcha_html  # noqa: E402
from app.scrapers.proxy_provider import get_proxy_provider  # noqa: E402

DEFAULT_URL = (
    "https://shopee.co.th/%E0%B8%A3%E0%B8%AD%E0%B8%87%E0%B9%80%E0%B8%97%E0%B9%89%E0%B8%B2%E0%B9%81%E0%B8%95%E0%B8%B0-"
    "%E0%B8%9E%E0%B8%B7%E0%B9%89%E0%B8%99%E0%B8%99%E0%B8%B4%E0%B9%88%E0%B8%A1-%E0%B8%81%E0%B8%B1%E0%B8%99%E0%B8%A5%E0%B8%B7"
    "%E0%B9%88%E0%B8%99-%E0%B9%80%E0%B8%AB%E0%B8%A1%E0%B8%B2%E0%B8%B0%E0%B8%81%E0%B8%B1%E0%B8%9A%E0%B9%83%E0%B8%AA%E0%B9%88"
    "%E0%B9%83%E0%B8%99%E0%B8%9A%E0%B9%89%E0%B8%B2%E0%B8%99-%E0%B8%AB%E0%B9%89%E0%B8%AD%E0%B8%87%E0%B8%99%E0%B9%89%E0%B9%8D"
    "%E0%B8%B2%E0%B8%81%E0%B8%A5%E0%B8%B2%E0%B8%87%E0%B9%81%E0%B8%88%E0%B9%89%E0%B8%87-%E0%B8%AA%E0%B9%8D%E0%B8%B2%E0%B8%AB"
    "%E0%B8%A3%E0%B8%B1%E0%B8%9A%E0%B8%84%E0%B8%B9%E0%B9%88%E0%B8%A3%E0%B8%B1%E0%B8%81-X023-i.401216213.27607640618"
)
_PDP_API_FRAGMENTS = ["pdp/get_pc", "/item/get"]
_WAIT_SECONDS = 45
OUT_PATH = Path(__file__).resolve().parent.parent / "logs" / "shopee_th_nodriver_pilot_result.json"


def _proxy_server_url() -> str | None:
    proxy = get_proxy_provider().next_proxy(country="th")
    if proxy is None:
        return None
    scheme, _, host = proxy["server"].partition("://")
    if "username" in proxy:
        return f"{scheme}://{proxy['username']}:{proxy['password']}@{host}"
    return proxy["server"]


async def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    captured: dict = {}
    proxy_server = _proxy_server_url()
    print(f"Using proxy: {'yes (Bright Data, country=th)' if proxy_server else 'no'}")

    browser = await nodriver.start(headless=False)
    try:

        async def on_response(evt: network.ResponseReceived) -> None:
            if "request_id" in captured:
                return
            if any(fragment in evt.response.url for fragment in _PDP_API_FRAGMENTS):
                captured["request_id"] = evt.request_id
                captured["response_url"] = evt.response.url
                captured["response_status"] = evt.response.status

        print("Warming up: shopee.co.th home page...")
        home_tab = await browser.create_context("https://shopee.co.th", proxy_server=proxy_server)
        await home_tab.send(network.enable())
        home_tab.add_handler(network.ResponseReceived, on_response)
        await home_tab.sleep(3)

        print(f"Navigating: {url}")
        tab = await home_tab.get(url)
        await tab.send(network.enable())
        tab.add_handler(network.ResponseReceived, on_response)

        for _ in range(int(_WAIT_SECONDS / 0.5)):
            if "request_id" in captured:
                break
            await tab.sleep(0.5)

        html = await tab.get_content()
        captcha_detected = is_captcha_html(html)

        api_body = None
        api_body_error = None
        if "request_id" in captured:
            # Give the browser a moment to finish buffering the body before
            # asking for it — get_response_body can race the event firing.
            await tab.sleep(1)
            try:
                body_text, is_base64 = await tab.send(network.get_response_body(request_id=captured["request_id"]))
                api_body = json.loads(body_text) if not is_base64 else {"__base64__": True}
            except Exception as exc:  # noqa: BLE001 - want the raw failure for inspection
                api_body_error = f"{type(exc).__name__}: {exc}"

        await tab.save_screenshot(str(OUT_PATH.with_suffix(".png")))

        result = {
            "product_url_requested": url,
            "final_page_url": tab.url,
            "captcha_signature_detected": captcha_detected,
            "pdp_api_call_observed": "request_id" in captured,
            "pdp_api_response_status": captured.get("response_status"),
            "pdp_api_response_url": captured.get("response_url"),
            "pdp_api_body_top_level_keys": list(api_body.keys()) if isinstance(api_body, dict) else None,
            "pdp_api_body_error_code": (api_body or {}).get("error") if isinstance(api_body, dict) else None,
            "pdp_api_body_fetch_error": api_body_error,
            "pdp_api_body": api_body,
            "html_len": len(html),
        }
    finally:
        browser.stop()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote result to {OUT_PATH}")
    print(f"Screenshot: {OUT_PATH.with_suffix('.png')}")
    print(f"CAPTCHA signature detected: {result['captcha_signature_detected']}")
    print(f"pdp/get_pc call observed: {result['pdp_api_call_observed']}")
    print(f"pdp/get_pc response status: {result['pdp_api_response_status']}")
    print(f"pdp/get_pc body top-level keys: {result['pdp_api_body_top_level_keys']}")
    print(f"pdp/get_pc body error code (if any): {result['pdp_api_body_error_code']}")


if __name__ == "__main__":
    asyncio.run(main())
