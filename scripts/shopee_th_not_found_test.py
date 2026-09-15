"""One-off test: confirm ProductNotFoundError.raw for a dead shopee_th item
matches {"bff_meta": null, "error": <code>, "error_msg": null, "data": null}
via either transport that can observe it: curl_cffi (what shopee_th actually
falls back to for a dead item — see ShopeeTHScraper.fetch_pdp_via_apify's own
comment) or Bright Data's Web Unlocker API (fetch_pdp_via_unlocker_api, the
transport scripts/shopee_br_not_found_test.py uses for shopee_br) — both
share the same _parse_pdp_page_html/_PDP_FETCH_ERROR check in
_shopee_common.py, so either should observe the same error shape.

Run from shopee_scraper_api/ so .env is found:

    python scripts/shopee_th_not_found_test.py [dead_product_url] [--transport curl_cffi|unlocker_api]
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.scrapers import http_pool  # noqa: E402
from app.scrapers.base import CaptchaBlockedError, ProductNotFoundError, ScraperError  # noqa: E402
from app.scrapers.sites.shopee_th import ShopeeTHScraper  # noqa: E402

# Fabricated shop_id/item_id — same "confirmed dead id" approach
# shopee_br_not_found_test.py uses.
DEFAULT_URL = "https://shopee.co.th/product-i.999999999.999999999"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", nargs="?", default=DEFAULT_URL)
    parser.add_argument("--transport", choices=["curl_cffi", "unlocker_api"], default="curl_cffi")
    args = parser.parse_args()

    scraper = ShopeeTHScraper()
    http_pool.startup()

    print(f"Fetching ({args.transport} transport): {args.url}")
    try:
        try:
            if args.transport == "unlocker_api":
                pdp = await scraper.fetch_pdp_via_unlocker_api(args.url)
            else:
                pdp = await scraper.fetch_pdp_via_curl_cffi(args.url)
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
