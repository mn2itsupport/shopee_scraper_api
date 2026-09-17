"""One-off pilot: does Bright Data's Scraping Browser (BROWSER_MODE=brightdata_cdp),
with its proprietary Captcha.waitForSolve hook wired in (BrightDataCaptchaSolver),
get past Shopee TH's anti-bot wall on the live pdp/get_pc call?

Prior testing (see shopee_th.py's browser_mode_override comment) already hit this
wall via CDP mode WITHOUT the solver engaged — this pilot isolates whether wiring
the solver changes the outcome, or whether TH's block is a silent risk-control
rejection with no actual CAPTCHA to solve (same shape as the {"error": 90309999}
case documented in _shopee_common.py's _parse_api_body).

Run from shopee_scraper_api/ so .env (BRIGHTDATA_WS_ENDPOINT) is found:

    python scripts/shopee_th_cdp_mode_pilot.py [product_url]

Self-contained — connects its own Playwright driver to Bright Data's Scraping
Browser over CDP rather than touching browser_pool's shared instance, so this
doesn't require restarting or reconfiguring the running app. Mirrors
shopee_br_cdp_mode_pilot.py exactly, pointed at ShopeeTHScraper instead — does
not touch shopee_br.py or any Brazil-specific code/config.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright  # noqa: E402

from app.config import settings  # noqa: E402
from app.scrapers.base import CaptchaBlockedError, ProductNotFoundError, ScraperError  # noqa: E402
from app.scrapers.sites.shopee_th import ShopeeTHScraper  # noqa: E402

DEFAULT_URL = (
    "https://shopee.co.th/%E0%B8%A3%E0%B8%AD%E0%B8%87%E0%B9%80%E0%B8%97%E0%B9%89%E0%B8%B2%E0%B9%81%E0%B8%95%E0%B8%B0-"
    "%E0%B8%9E%E0%B8%B7%E0%B9%89%E0%B8%99%E0%B8%99%E0%B8%B4%E0%B9%88%E0%B8%A1-%E0%B8%81%E0%B8%B1%E0%B8%99%E0%B8%A5%E0%B8%B7"
    "%E0%B9%88%E0%B8%99-%E0%B9%80%E0%B8%AB%E0%B8%A1%E0%B8%B2%E0%B8%B0%E0%B8%81%E0%B8%B1%E0%B8%9A%E0%B9%83%E0%B8%AA%E0%B9%88"
    "%E0%B9%83%E0%B8%99%E0%B8%9A%E0%B9%89%E0%B8%B2%E0%B8%99-%E0%B8%AB%E0%B9%89%E0%B8%AD%E0%B8%87%E0%B8%99%E0%B9%89%E0%B9%8D"
    "%E0%B8%B2%E0%B8%81%E0%B8%A5%E0%B8%B2%E0%B8%87%E0%B9%81%E0%B8%88%E0%B9%89%E0%B8%87-%E0%B8%AA%E0%B9%8D%E0%B8%B2%E0%B8%AB"
    "%E0%B8%A3%E0%B8%B1%E0%B8%9A%E0%B8%84%E0%B8%B9%E0%B9%88%E0%B8%A3%E0%B8%B1%E0%B8%81-X023-i.401216213.27607640618"
    "?extraParams=%7B%22display_model_id%22%3A157414544557%2C%22model_selection_logic%22%3A3%7D"
)


async def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL

    if not settings.brightdata_ws_endpoint:
        print("BRIGHTDATA_WS_ENDPOINT is not set in .env — nothing to connect to.")
        return

    # Only affects captcha.py's get_captcha_solver() lookup within this
    # process — doesn't touch .env or the running app.
    settings.browser_mode = "brightdata_cdp"
    scraper = ShopeeTHScraper()

    print("Connecting to Bright Data Scraping Browser over CDP...")
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(settings.brightdata_ws_endpoint)
        try:
            context = await browser.new_context(locale=scraper.locale)
            try:
                print(f"Fetching (brightdata_cdp transport, solver wired): {url}")
                try:
                    pdp = await scraper.fetch_pdp(context, url)
                except ProductNotFoundError as exc:
                    print(f"\nRESULT: ProductNotFoundError\n  {exc}\n  raw: {exc.raw}")
                    return
                except CaptchaBlockedError as exc:
                    print(f"\nRESULT: CaptchaBlockedError — anti-bot wall hit (solver did not clear it).\n  {exc}")
                    return
                except ScraperError as exc:
                    print(f"\nRESULT: ScraperError\n  {exc}")
                    return
            finally:
                await context.close()
        finally:
            await browser.close()

    print("\nRESULT: success")
    print(f"  title:    {pdp.title}")
    print(f"  price:    {pdp.price}")
    print(f"  currency: {pdp.currency}")
    print(f"  rating:   {pdp.rating}")
    print(f"  sold:     {pdp.sold_count}")
    print(f"  images:   {len(pdp.image_urls)}")
    print(f"  raw top-level keys: {list(pdp.raw.keys())}")
    if "error" in pdp.raw or "bff_meta" in pdp.raw:
        print(f"  raw.error:      {pdp.raw.get('error')!r}")
        print(f"  raw.error_msg:  {pdp.raw.get('error_msg')!r}")
        print(f"  raw.bff_meta:   {'present' if pdp.raw.get('bff_meta') is not None else 'null'}")
    out_path = Path(__file__).resolve().parent.parent / "logs" / "shopee_th_cdp_mode_pilot_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(pdp.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFull PDPData (incl. raw) written to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
