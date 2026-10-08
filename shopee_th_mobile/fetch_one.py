"""Fetch one shopee.co.th product's mobile-web response (pdp/get_rw) straight
from the phone Chrome and save it — no agent or token needed.

    .venv\\Scripts\\python.exe -m shopee_th_mobile.fetch_one "https://shopee.co.th/product/989284521/43429338236" [out.json]
"""

import asyncio
import json
import sys
from pathlib import Path

from shopee_th_mobile.capture import capture_get_rw, product_url


def _sold(body: dict) -> str | None:
    review = (body.get("data") or {}).get("product_review") or {}
    return review.get("historical_sold_display") or review.get("sold_count_display")


async def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    url = product_url(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(f"shopee_th_mobile_{url.rsplit('.', 2)[-2]}_{url.rsplit('.', 1)[-1]}.json")
    endpoint, body = await capture_get_rw(url)
    out.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    item = (body.get("data") or {}).get("item") or {}
    print(f"endpoint: {endpoint}")
    print(f"title:    {item.get('title')}")
    print(f"price:    {item.get('price')} (x100000 {item.get('currency')})")
    print(f"sold:     {_sold(body)}")
    print(f"saved:    {out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
