"""One-off pilot: does the browser-driven local transport (real Playwright
page + live pdp/get_pc network capture) work against Shopee SG, or does it
hit the same anti-bot wall shopee_th/br did?

Run from shopee_scraper_api/ so .env is found:

    python scripts/shopee_sg_local_mode_pilot.py [product_url]

Bypasses the registry/SHOPEE_SG_BROWSER_MODE_OVERRIDE entirely — calls
ShopeeSGScraper.fetch_pdp() directly, once, no CAPTCHA retry loop — so a
single run gives a clean pass/fail signal without waiting through
scrape_with_retries' multi-attempt backoff.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.scrapers import browser_pool  # noqa: E402
from app.scrapers.base import CaptchaBlockedError, ScraperError  # noqa: E402
from app.scrapers.sites.shopee_sg import ShopeeSGScraper  # noqa: E402

DEFAULT_URL = "https://shopee.sg/product-i.10208.14298826796"


async def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    scraper = ShopeeSGScraper()

    print("Starting browser pool...")
    await browser_pool.startup()
    try:
        print(f"Fetching (local browser transport): {url}")
        async with browser_pool.acquire_context(
            scraper.site_key, scraper.locale, scraper.timezone_id, scraper.geolocation, scraper.unlocker_country
        ) as context:
            try:
                pdp = await scraper.fetch_pdp(context, url)
            except CaptchaBlockedError as exc:
                print(f"\nRESULT: CaptchaBlockedError — anti-bot wall hit.\n  {exc}")
                return
            except ScraperError as exc:
                print(f"\nRESULT: ScraperError\n  {exc}")
                return

        print("\nRESULT: success")
        print(f"  title:    {pdp.title}")
        print(f"  price:    {pdp.price}")
        print(f"  currency: {pdp.currency}")
        print(f"  rating:   {pdp.rating}")
        print(f"  sold:     {pdp.sold_count}")
        print(f"  images:   {len(pdp.image_urls)}")
        print(f"  raw top-level keys: {list(pdp.raw.keys())}")
        out_path = Path(__file__).resolve().parent.parent / "logs" / "shopee_sg_local_mode_pilot_result.json"
        out_path.parent.mkdir(exist_ok=True)
        out_path.write_text(json.dumps(pdp.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nFull PDPData (incl. raw) written to {out_path}")
    finally:
        await browser_pool.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
