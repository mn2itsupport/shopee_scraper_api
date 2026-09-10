"""One-off pilot: does the browser-driven local transport (real Playwright
page + live pdp/get_pc network capture) work against Shopee BR, or does it
hit the same anti-bot wall shopee_th did?

Run from shopee_scraper_api/ so .env is found:

    python scripts/shopee_br_local_mode_pilot.py [product_url]

Bypasses the registry/SHOPEE_BR_BROWSER_MODE_OVERRIDE entirely — calls
ShopeeBRScraper.fetch_pdp() directly, once, no CAPTCHA retry loop — so a
single run gives a clean pass/fail signal without waiting through
scrape_with_retries' multi-attempt backoff. Uses PROXY_MODE=brightdata_residential
(BRIGHTDATA_RESIDENTIAL_ZONE, already configured for BR) via the same
acquire_context() path the real app uses, so the result reflects what
SHOPEE_BR_BROWSER_MODE_OVERRIDE=local would actually do in production.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.scrapers import browser_pool  # noqa: E402
from app.scrapers.base import CaptchaBlockedError, ScraperError  # noqa: E402
from app.scrapers.sites.shopee_br import ShopeeBRScraper  # noqa: E402

DEFAULT_URL = (
    "https://shopee.com.br/2026-Mais-Recente-Smartwatch-S10-Pro-Rel%C3%B3gio-S%C3%A9rie-10-"
    "2.08-Polegadas-Sem-Fio-Bluetooth-Esportes-ALKF-i.1432047820.44110609035"
)


async def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    scraper = ShopeeBRScraper()

    print(f"Starting browser pool (proxy_mode should be brightdata_residential)...")
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
        if "error" in pdp.raw or "bff_meta" in pdp.raw:
            print(f"  raw.error:      {pdp.raw.get('error')!r}")
            print(f"  raw.error_msg:  {pdp.raw.get('error_msg')!r}")
            print(f"  raw.bff_meta:   {'present' if pdp.raw.get('bff_meta') is not None else 'null'}")
        out_path = Path(__file__).resolve().parent.parent / "logs" / "shopee_br_local_mode_pilot_result.json"
        out_path.write_text(json.dumps(pdp.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nFull PDPData (incl. raw) written to {out_path}")
    finally:
        await browser_pool.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
