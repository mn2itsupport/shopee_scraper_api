"""One-off pilot: does Bright Data's Scraping Browser (BROWSER_MODE=brightdata_cdp)
get past Shopee BR's anti-bot wall on the live pdp/get_pc call, where the local
Playwright transport (see shopee_br_local_mode_pilot.py) got blocked 2/3 times?

Run from shopee_scraper_api/ so .env (BRIGHTDATA_WS_ENDPOINT) is found:

    python scripts/shopee_br_cdp_mode_pilot.py [product_url]

Self-contained — connects its own Playwright driver to Bright Data's Scraping
Browser over CDP rather than touching browser_pool's shared instance, so this
doesn't require restarting or reconfiguring the running app. Mirrors
browser_pool.ManagedContext's brightdata_cdp branch exactly (new_context with
just locale + storage_state — no local proxy/UA/stealth, Bright Data handles
fingerprinting/proxy rotation on its own side) and monkeypatches
settings.browser_mode so captcha.py's get_captcha_solver() picks
BrightDataCaptchaSolver (Captcha.waitForSolve) instead of the NoOp solver
every other transport in this project falls back to.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright  # noqa: E402

from app.config import settings  # noqa: E402
from app.scrapers.base import CaptchaBlockedError, ScraperError  # noqa: E402
from app.scrapers.sites.shopee_br import ShopeeBRScraper  # noqa: E402

DEFAULT_URL = (
    "https://shopee.com.br/2026-Mais-Recente-Smartwatch-S10-Pro-Rel%C3%B3gio-S%C3%A9rie-10-"
    "2.08-Polegadas-Sem-Fio-Bluetooth-Esportes-ALKF-i.1432047820.44110609035"
)


async def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL

    if not settings.brightdata_ws_endpoint:
        print("BRIGHTDATA_WS_ENDPOINT is not set in .env — nothing to connect to.")
        return

    # Only affects captcha.py's get_captcha_solver() lookup within this
    # process — doesn't touch .env or the running app.
    settings.browser_mode = "brightdata_cdp"
    scraper = ShopeeBRScraper()

    print("Connecting to Bright Data Scraping Browser over CDP...")
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(settings.brightdata_ws_endpoint)
        try:
            context = await browser.new_context(locale=scraper.locale)
            try:
                print(f"Fetching (brightdata_cdp transport): {url}")
                try:
                    pdp = await scraper.fetch_pdp(context, url)
                except CaptchaBlockedError as exc:
                    print(f"\nRESULT: CaptchaBlockedError — anti-bot wall hit.\n  {exc}")
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
    out_path = Path(__file__).resolve().parent.parent / "logs" / "shopee_br_cdp_mode_pilot_result.json"
    out_path.write_text(json.dumps(pdp.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFull PDPData (incl. raw) written to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
