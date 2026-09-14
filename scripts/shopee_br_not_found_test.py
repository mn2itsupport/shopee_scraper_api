"""One-off test: confirm ProductNotFoundError.raw for a dead shopee_br item
matches {"bff_meta": null, "error": <code>, "error_msg": null, "data": null}
via the transport shopee_br actually runs in (brightdata_unlocker_api, per
.env's SHOPEE_BR_BROWSER_MODE_OVERRIDE="" falling back to global BROWSER_MODE).

Run from shopee_scraper_api/ so .env is found:

    python scripts/shopee_br_not_found_test.py [dead_product_url]
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.scrapers import http_pool  # noqa: E402
from app.scrapers.base import CaptchaBlockedError, ProductNotFoundError, ScraperError  # noqa: E402
from app.scrapers.sites.shopee_br import ShopeeBRScraper  # noqa: E402

# Fabricated shop_id/item_id — same "confirmed dead id" approach the
# _PDP_FETCH_ERROR comment describes for shopee_vn, applied to shopee_br.
DEFAULT_URL = "https://shopee.com.br/product-i.999999999.999999999"


async def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    scraper = ShopeeBRScraper()
    http_pool.startup()

    print(f"Fetching (brightdata_unlocker_api transport): {url}")
    try:
        try:
            pdp = await scraper.fetch_pdp_via_unlocker_api(url)
        except ProductNotFoundError as exc:
            print("\nRESULT: ProductNotFoundError (expected)")
            print(f"  message: {exc}")
            print(f"  exc.raw: {json.dumps(exc.raw, ensure_ascii=False)}")
            expected_shape = {"bff_meta", "error", "error_msg", "data"}
            if set(exc.raw.keys()) == expected_shape and exc.raw.get("data") is None:
                print("  shape check: OK (bff_meta/error/error_msg/data, data=null)")
            else:
                print("  shape check: MISMATCH")
            return
        except CaptchaBlockedError as exc:
            print(f"\nRESULT: CaptchaBlockedError — anti-bot wall hit, inconclusive.\n  {exc}")
            return
        except ScraperError as exc:
            print(f"\nRESULT: ScraperError (unexpected for a dead item)\n  {exc}")
            return

        print("\nRESULT: success (unexpected — this URL wasn't treated as dead)")
        print(f"  title: {pdp.title}")
        print(f"  raw:   {json.dumps(pdp.raw, ensure_ascii=False)[:500]}")
    finally:
        await http_pool.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
