"""One-off check: fetch a shopee_th PDP URL through the real registry path
(scrape_with_retries -> apify transport, per .env's
SHOPEE_TH_BROWSER_MODE_OVERRIDE=apify) and write the exact API response
shape ({"status", "data"}) to a file for inspection.

Run from shopee_scraper_api/ so .env is found:

    python scripts/shopee_th_check_response.py <url> [output_path]
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.scrapers import http_pool  # noqa: E402
from app.scrapers.base import CaptchaBlockedError, ProductNotFoundError, ScraperError  # noqa: E402
from app.scrapers.registry import scrape_with_retries  # noqa: E402

DEFAULT_OUTPUT = Path.home() / "Desktop" / "response.txt"


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/shopee_th_check_response.py <url> [output_path]")
        sys.exit(1)
    url = sys.argv[1]
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTPUT

    http_pool.startup()
    try:
        try:
            pdp = await scrape_with_retries("shopee_th", url)
            response = {"status": "success", "data": pdp.raw}
        except ProductNotFoundError as exc:
            response = {"status": "success", "data": exc.raw}
        except CaptchaBlockedError as exc:
            response = {"status": "captcha_blocked", "error": str(exc)}
        except ScraperError as exc:
            response = {"status": "failed", "error": str(exc)}

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(response, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote response to {out_path}")
        print(json.dumps(response, indent=2, ensure_ascii=False)[:2000])
    finally:
        await http_pool.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
