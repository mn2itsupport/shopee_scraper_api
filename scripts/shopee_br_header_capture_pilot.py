"""One-off, read-only pilot: capture the actual outgoing pdp/get_pc request
headers for a Brazil product via the same real-browser (Bright Data Scraping
Browser / CDP) path used by shopee_th_header_capture_pilot.py, purely to
compare af-ac-enc-dat/af-ac-enc-sz-token shape against the TH capture.

Does NOT modify shopee_br.py, _shopee_common.py, or any other existing file,
and does not change BR's production transport (still brightdata_unlocker_api
per .env) — this is a standalone, additive, read-only comparison script only.

Run from shopee_scraper_api/ so .env (BRIGHTDATA_WS_ENDPOINT) is found:

    python scripts/shopee_br_header_capture_pilot.py [product_url]
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import Route, async_playwright  # noqa: E402

from app.config import settings  # noqa: E402

DEFAULT_URL = (
    "https://shopee.com.br/2026-Mais-Recente-Smartwatch-S10-Pro-Rel%C3%B3gio-S%C3%A9rie-10-"
    "2.08-Polegadas-Sem-Fio-Bluetooth-Esportes-ALKF-i.1432047820.44110609035"
)

_PDP_API_FRAGMENTS = ["pdp/get_pc", "/item/get"]
_LIKELY_ANTI_CRAWL_HINTS = ["af-ac", "token", "sign", "sec-fetch", "x-sap", "x-api", "gid", "csrf"]


async def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL

    if not settings.brightdata_ws_endpoint:
        print("BRIGHTDATA_WS_ENDPOINT is not set in .env — nothing to connect to.")
        return

    captured: dict = {}

    async def handle_pdp_route(route: Route) -> None:
        if "request_headers" not in captured:
            captured["request_headers"] = await route.request.all_headers()
            captured["request_url"] = route.request.url
            captured["request_method"] = route.request.method
            post_data = route.request.post_data
            captured["request_post_data"] = post_data[:2000] if post_data else None
        if "task" in captured:
            await route.continue_()
            return
        api_response = await route.fetch()
        captured["task"] = asyncio.ensure_future(api_response.json())
        captured["response_status"] = api_response.status
        await route.fulfill(response=api_response)

    print("Connecting to Bright Data Scraping Browser over CDP...")
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(settings.brightdata_ws_endpoint)
        try:
            context = await browser.new_context(locale="pt-BR")
            try:
                page = await context.new_page()
                for fragment in _PDP_API_FRAGMENTS:
                    await page.route(f"**/*{fragment}*", handle_pdp_route)

                print(f"Navigating: {url}")
                await page.goto(url, timeout=settings.scrape_timeout_seconds * 1000, wait_until="domcontentloaded")

                for _ in range(int(settings.scrape_timeout_seconds / 0.5)):
                    if "task" in captured:
                        break
                    await asyncio.sleep(0.5)

                if "task" not in captured:
                    print("\nNo pdp/get_pc call observed within the timeout — nothing to inspect.")
                    return

                body = await captured["task"]
            finally:
                await context.close()
        finally:
            await browser.close()

    print(f"\nRequest URL:    {captured['request_url']}")
    print(f"Request method: {captured['request_method']}")
    print(f"Response status (as served by Bright Data): {captured.get('response_status')}")
    print(f"Response body top-level keys: {list(body.keys())}")
    print(f"Response body (first 500 chars): {json.dumps(body, ensure_ascii=False)[:500]}")

    headers = captured["request_headers"]
    print(f"\nOutgoing request headers ({len(headers)} total):")
    flagged = []
    for name, value in sorted(headers.items()):
        marker = ""
        if any(hint in name.lower() for hint in _LIKELY_ANTI_CRAWL_HINTS):
            marker = "  <-- anti-crawl/session-signature candidate"
            flagged.append(name)
        display_value = value if len(value) <= 120 else f"{value[:117]}... ({len(value)} chars total)"
        print(f"  {name}: {display_value}{marker}")

    print(f"\nFlagged as likely anti-crawl/session/signature headers: {flagged or 'none matched the naming heuristic'}")

    out_path = Path(__file__).resolve().parent.parent / "logs" / "shopee_br_header_capture_pilot_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "request_url": captured["request_url"],
                "request_method": captured["request_method"],
                "request_headers": headers,
                "request_post_data": captured.get("request_post_data"),
                "response_status": captured.get("response_status"),
                "response_body": body,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\nFull capture written to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
