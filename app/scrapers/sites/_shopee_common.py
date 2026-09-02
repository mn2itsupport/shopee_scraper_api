"""Shared logic for every Shopee country adapter (shopee_br.py, shopee_th.py,
shopee_vn.py, ...). Shopee runs the same platform — same PDP JSON API shape,
same `-i.<shopid>.<itemid>` URL pattern, same schema.org Product block, same
CAPTCHA wall — under a different domain, locale, and currency per country.
Country adapters are just class-attribute subclasses of ShopeeScraper below;
add a new country by subclassing, not by copying this file.

Strategy: rather than scraping the rendered DOM (fragile, changes with every
frontend deploy), we open the product page in a real browser and listen for
the network response Shopee's own frontend makes to populate the page — its
internal PDP JSON API (URL pattern containing "pdp/get_pc" or "/item/get").
That JSON is far more stable than DOM structure. If it isn't observed within
the timeout (layout/API change, or a CAPTCHA wall), we fall back to a
best-effort extraction from the rendered page's meta tags.
"""

import asyncio
import json
import re

import httpx
from playwright.async_api import BrowserContext, Route

from app.config import settings
from app.models.schemas import PDPData
from app.scrapers.base import BaseScraper, CaptchaBlockedError, ProductNotFoundError, ScraperError
from app.scrapers.captcha import get_captcha_solver, is_captcha_html, is_captcha_page, strip_script_and_style

_PDP_API_FRAGMENTS = ["pdp/get_pc", "/item/get"]
_URL_ID_PATTERN = re.compile(r"-i\.(\d+)\.(\d+)")
_UNLOCKER_API_URL = "https://api.brightdata.com/request"
_LD_JSON_BLOCK = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.IGNORECASE | re.DOTALL)


class ShopeeScraper(BaseScraper):
    """Base class for a Shopee country adapter. Subclasses set:

    site_key, base_domain, locale, timezone_id, geolocation (from BaseScraper)
    default_currency: used when a response omits its own currency field.
    not_found_signature: lowercased localized "this product doesn't exist"
        copy, present unconditionally in Shopee's bundled i18n dictionary on
        every page for that locale — same false-positive shape as the
        CAPTCHA signature match, so ld+json is checked first and is
        authoritative when present; leave "" to skip this check.
    """

    default_currency: str = "USD"
    not_found_signature: str = ""

    async def fetch_pdp(self, context: BrowserContext, url: str) -> PDPData:
        captured: dict = {}

        async def handle_pdp_route(route: Route) -> None:
            if "task" in captured:
                await route.continue_()
                return
            # Fetch through Playwright's own request layer (route.fetch())
            # rather than reading the browser's live-buffered response body
            # via response.json() — the latter intermittently fails with a
            # CDP "No data found for resource with given identifier" error,
            # reproduced reliably when routed through a TLS-intercepting
            # proxy like Bright Data's Web Unlocker. Re-serving the fetched
            # response via fulfill() keeps the page's own JS unaffected.
            api_response = await route.fetch()
            captured["task"] = asyncio.ensure_future(api_response.json())
            await route.fulfill(response=api_response)

        page = await context.new_page()
        for fragment in _PDP_API_FRAGMENTS:
            await page.route(f"**/*{fragment}*", handle_pdp_route)

        try:
            await page.goto(url, timeout=settings.scrape_timeout_seconds * 1000, wait_until="domcontentloaded")

            await self._require_no_captcha(page)

            # Give the page's own XHR call a moment to land after navigation.
            for _ in range(int(settings.scrape_timeout_seconds / 0.5)):
                if "task" in captured:
                    break
                await asyncio.sleep(0.5)

            await self._require_no_captcha(page)

            if "task" in captured:
                try:
                    body = await captured["task"]
                except Exception as exc:
                    raise ScraperError(f"Could not decode Shopee PDP API response: {exc}") from exc
                return self._parse_api_body(body, url)

            return await self._parse_dom_fallback(page, url)
        finally:
            await page.close()

    async def fetch_pdp_via_unlocker_api(self, url: str) -> PDPData:
        try:
            async with httpx.AsyncClient(timeout=settings.scrape_timeout_seconds) as client:
                resp = await client.post(
                    _UNLOCKER_API_URL,
                    headers={"Authorization": f"Bearer {settings.brightdata_api_token}"},
                    json={"zone": settings.brightdata_unlocker_zone, "url": url, "format": "raw"},
                )
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ScraperError(f"Web Unlocker API request failed: {exc}") from exc

        # Bright Data reports a failed proxy leg (auth, suspended account,
        # target unreachable, ...) as HTTP 200 with an empty body and the
        # actual error only in this header — raise_for_status() above can't
        # see it, so an unchecked empty body would otherwise silently parse
        # as a "fallback" scrape result instead of a clear failure.
        brd_error = resp.headers.get("x-brd-error")
        if brd_error:
            raise ScraperError(f"Web Unlocker API request failed: {brd_error}")

        html = resp.text

        if is_captcha_html(html):
            raise CaptchaBlockedError("Shopee showed a verification/anti-bot wall")

        # Check ld+json first — it's authoritative when present, so a real
        # product page never falls through to the not-found check below.
        product = self._extract_ld_json_product(html)
        if product is not None:
            return self._parse_ld_json_product(product, url)

        if self.not_found_signature and self.not_found_signature in strip_script_and_style(html).lower():
            raise ProductNotFoundError("Shopee reports this product does not exist")

        return self._parse_html_fallback(html, url)

    def _extract_ld_json_product(self, html: str) -> dict | None:
        # Shopee embeds standard schema.org Product structured data for SEO —
        # more stable than scraping rendered markup, and (unlike the internal
        # pdp/get_pc XHR the browser-based transport captures) already
        # present in the server-rendered HTML this API returns.
        for match in _LD_JSON_BLOCK.finditer(html):
            try:
                data = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("@type") == "Product":
                return data
        return None

    def _parse_ld_json_product(self, item: dict, url: str) -> PDPData:
        offers = item.get("offers") or {}
        try:
            price = float(offers["price"])
        except (KeyError, TypeError, ValueError):
            price = None

        rating_raw = (item.get("aggregateRating") or {}).get("ratingValue")
        try:
            rating = float(rating_raw) if rating_raw is not None else None
        except (TypeError, ValueError):
            rating = None

        image = item.get("image")
        images = [image] if isinstance(image, str) else [i for i in (image or []) if isinstance(i, str)]

        match = _URL_ID_PATTERN.search(url)
        external_id = f"{match.group(1)}.{match.group(2)}" if match else None

        return PDPData(
            site_key=self.site_key,
            product_url=url,
            external_product_id=external_id,
            title=item.get("name"),
            price=price,
            currency=offers.get("priceCurrency", self.default_currency),
            rating=rating,
            # Not present in Shopee's schema.org Product block — the browser
            # transport's internal-API capture is the only source for this.
            sold_count=None,
            image_urls=images,
            raw=item,
        )

    def _parse_html_fallback(self, html: str, url: str) -> PDPData:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        og_desc_match = re.search(
            r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"', html, re.IGNORECASE
        )

        match = _URL_ID_PATTERN.search(url)
        external_id = f"{match.group(1)}.{match.group(2)}" if match else None

        return PDPData(
            site_key=self.site_key,
            product_url=url,
            external_product_id=external_id,
            title=title_match.group(1).strip() if title_match else None,
            raw={"fallback": True, "og_description": og_desc_match.group(1) if og_desc_match else None},
        )

    async def _require_no_captcha(self, page) -> None:
        if not await is_captcha_page(page):
            return
        if await get_captcha_solver().solve(page):
            return
        raise CaptchaBlockedError("Shopee showed a verification/anti-bot wall")

    def _parse_api_body(self, body: dict, url: str) -> PDPData:
        item = body.get("data", {}).get("item") or body.get("data", {})
        if not item:
            raise ScraperError("Shopee PDP API response had no item data")

        price_raw = item.get("price") or item.get("price_min")
        price = price_raw / 100000 if isinstance(price_raw, (int, float)) else None

        images = [
            f"https://cf.{self.base_domain}/file/{img}"
            for img in item.get("images", [])
            if isinstance(img, str)
        ]

        match = _URL_ID_PATTERN.search(url)
        external_id = f"{match.group(1)}.{match.group(2)}" if match else None

        return PDPData(
            site_key=self.site_key,
            product_url=url,
            external_product_id=external_id,
            title=item.get("name"),
            price=price,
            currency=item.get("currency", self.default_currency),
            rating=(item.get("item_rating") or {}).get("rating_star"),
            sold_count=item.get("historical_sold") or item.get("sold"),
            image_urls=images,
            raw=item,
        )

    async def _parse_dom_fallback(self, page, url: str) -> PDPData:
        title = await page.title()
        # query_selector (unlike locator.get_attribute) returns None immediately
        # instead of waiting out the default actionability timeout when absent.
        meta = await page.query_selector('meta[property="og:description"]')
        og_description = await meta.get_attribute("content") if meta else None

        match = _URL_ID_PATTERN.search(url)
        external_id = f"{match.group(1)}.{match.group(2)}" if match else None

        return PDPData(
            site_key=self.site_key,
            product_url=url,
            external_product_id=external_id,
            title=title,
            raw={"fallback": True, "og_description": og_description},
        )
