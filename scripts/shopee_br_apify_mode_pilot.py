"""One-off pilot: does the Apify actor (gio21/shopee-product-detail, already
used for shopee_th/vn via BROWSER_MODE=apify) return real price/rating data
for Shopee BR, given both browser-based Bright Data transports
(BROWSER_MODE=local, brightdata_cdp) got CaptchaBlockedError on every
attempt (see shopee_br_local_mode_pilot.py, shopee_br_cdp_mode_pilot.py)?

Run from shopee_scraper_api/ so .env (APIFY_API_TOKEN) is found:

    python scripts/shopee_br_apify_mode_pilot.py [product_url]

Calls ShopeeBRScraper.fetch_pdp_via_apify() directly — no browser, no
registry, no SHOPEE_BR_BROWSER_MODE_OVERRIDE needed — so this doesn't touch
the running app or its config.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.scrapers import http_pool  # noqa: E402
from app.scrapers.base import ScraperError  # noqa: E402
from app.scrapers.sites.shopee_br import ShopeeBRScraper  # noqa: E402

DEFAULT_URL = (
    "https://shopee.com.br/2026-Mais-Recente-Smartwatch-S10-Pro-Rel%C3%B3gio-S%C3%A9rie-10-"
    "2.08-Polegadas-Sem-Fio-Bluetooth-Esportes-ALKF-i.1432047820.44110609035"
)


async def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    scraper = ShopeeBRScraper()

    http_pool.startup()
    try:
        print(f"Fetching (apify transport): {url}")
        try:
            pdp = await scraper.fetch_pdp_via_apify(url)
        except ScraperError as exc:
            print(f"\nRESULT: ScraperError\n  {exc}")
            return
    finally:
        await http_pool.shutdown()

    print("\nRESULT: success")
    print(f"  title:    {pdp.title}")
    print(f"  price:    {pdp.price}")
    print(f"  currency: {pdp.currency}")
    print(f"  rating:   {pdp.rating}")
    print(f"  sold:     {pdp.sold_count}")
    print(f"  images:   {len(pdp.image_urls)}")
    print(f"  raw top-level keys: {list(pdp.raw.keys())}")
    out_path = Path(__file__).resolve().parent.parent / "logs" / "shopee_br_apify_mode_pilot_result.json"
    out_path.write_text(json.dumps(pdp.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFull PDPData (incl. raw) written to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
