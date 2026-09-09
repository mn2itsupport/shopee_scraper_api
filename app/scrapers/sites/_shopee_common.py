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
from app.scrapers.http_pool import get_client

_PDP_API_FRAGMENTS = ["pdp/get_pc", "/item/get"]
_URL_ID_PATTERN = re.compile(r"-i\.(\d+)\.(\d+)")
_UNLOCKER_API_URL = "https://api.brightdata.com/request"
_DATASET_TRIGGER_URL = "https://api.brightdata.com/datasets/v3/trigger"
_DATASET_PROGRESS_URL = "https://api.brightdata.com/datasets/v3/progress/{snapshot_id}"
_DATASET_SNAPSHOT_URL = "https://api.brightdata.com/datasets/v3/snapshot/{snapshot_id}"
# Seconds between progress polls while waiting for a dataset job to finish —
# these run on Bright Data's own infrastructure independent of our request,
# so there's nothing to gain from polling faster than this.
_DATASET_POLL_INTERVAL_SECONDS = 3
# {actor} is "owner~actor-name" (Apify's API uses "~" where the actor's own
# page URL uses "/") — run-sync-get-dataset-items runs the actor and hands
# back its output directly in the response body, no separate poll/snapshot
# step needed (unlike Bright Data's Dataset API above).
_APIFY_RUN_SYNC_URL = "https://api.apify.com/v2/actors/{actor}/run-sync-get-dataset-items"
# Apify's own run-sync endpoint returns its own 408 after 300s server-side —
# our client timeout needs enough margin above that to actually see the real
# response rather than giving up first (confirmed via live testing:
# settings.scrape_timeout_seconds' default 150s is too short for this actor,
# which can legitimately take longer than a direct browser/API scrape does).
_APIFY_TIMEOUT_SECONDS = 320
_LD_JSON_BLOCK = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.IGNORECASE | re.DOTALL)
# The unlocker/raw transport's PDP page embeds its own client-side fetch
# state (Shopee's "PDP BFF" call) in a <script type="text/mfe-initial-data">
# block. For a nonexistent shop_id/item_id, Shopee still serves the product
# page *shell* (not a redirect, not its own "not found" copy) but this fetch
# comes back with isError:true and a numeric error code — confirmed against
# a fabricated shopee_vn item_id (error code 266900002). Checking for the
# item_id's mere presence in the HTML doesn't work as a not-found signal:
# Shopee's own bootstrap state echoes the requested shop_id/item_id from the
# URL regardless of whether the fetch behind them succeeded.
_PDP_FETCH_ERROR = re.compile(r'"setPdpBffData":\{[^}]*"isError":true,"error":\{[^}]*"error":(\d+)')
# Same <script type="text/mfe-initial-data"> block also carries the actual
# PDP BFF data Shopee's frontend fetched, at
# initialState.DOMAIN_PDP.data.PDP_BFF_DATA.cachedMap["<shop_id>/<item_id>"]
# — the same {item, account, product_images, product_price, shop_detailed,
# installment_drawer, product_description, ...} shape _parse_api_body() gets
# from body["data"] on the live pdp/get_pc XHR (browser transport), just
# server-embedded instead of a live network response. Confirmed present on
# both shopee_th and shopee_vn pages that have no ld+json Product block, so
# this is a strictly better source than the <title>/og:description-only
# fallback: it has the real title and image ids even when item.price/
# item.item_rating/item.sold are nulled out (confirmed on both countries —
# Shopee withholds those specific fields from this transport regardless of
# item validity, not something fixable client-side).
_MFE_INITIAL_DATA_BLOCK = re.compile(
    r'<script[^>]+type="text/mfe-initial-data"[^>]*>(.*?)</script>', re.IGNORECASE | re.DOTALL
)
# Shopee also accepts an alternate PDP URL shape, /product/<shop_id>/<item_id>
# (no product slug, no "-i." marker) — same product, but Shopee's own server
# redirects it to the canonical "-i." URL before serving the page. Confirmed
# (shopee_vn) that redirect chain is much harder for the Web Unlocker to
# render: the canonical "-i." URL for the same item succeeds in 5-20s, while
# this format timed out on every one of 3 attempts (~236s) before giving up.
# Rewrite it to the canonical shape up front rather than pay that redirect
# cost on every request.
_ALT_PRODUCT_PATH_URL = re.compile(r"^(https?://[^/]+)/product/(\d+)/(\d+)(?:[/?#].*)?$", re.IGNORECASE)


def _normalize_shopee_url(url: str) -> str:
    match = _ALT_PRODUCT_PATH_URL.match(url)
    if not match:
        return url
    origin, shop_id, item_id = match.groups()
    return f"{origin}/product-i.{shop_id}.{item_id}"


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
        url = _normalize_shopee_url(url)
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
            if settings.shopee_warm_up_home_page:
                try:
                    await page.goto(
                        f"https://{self.base_domain}",
                        timeout=settings.scrape_timeout_seconds * 1000,
                        wait_until="domcontentloaded",
                    )
                    await self._require_no_captcha(page)
                    # Let the home page's own background scripts (fingerprint
                    # SDKs, anti-bot cookie issuance) finish rather than
                    # racing straight into the product navigation below.
                    await asyncio.sleep(1.5)
                except CaptchaBlockedError:
                    raise
                except Exception:
                    # Best-effort — a warm-up navigation failure (timeout,
                    # nav aborted, ...) shouldn't sink the real scrape below;
                    # worst case it proceeds exactly as it did before this
                    # existed.
                    pass

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
        url = _normalize_shopee_url(url)
        payload = {"zone": settings.brightdata_unlocker_zone, "url": url, "format": "raw"}
        if self.unlocker_country:
            payload["country"] = self.unlocker_country
        try:
            resp = await get_client().post(
                _UNLOCKER_API_URL,
                headers={"Authorization": f"Bearer {settings.brightdata_api_token}"},
                json=payload,
            )
            resp.raise_for_status()
        except httpx.TimeoutException as exc:
            # Confirmed (shopee_vn) that our own client-side timeout
            # (SCRAPE_TIMEOUT_SECONDS) routinely fires before Bright Data's
            # own ~90s-160s internal deadline on a page it can't unlock —
            # probed directly against Bright Data with a much longer client
            # timeout, the same request eventually comes back with
            # x-brd-error: 'waiting for selector "..." failed: timeout ...
            # exceeded', i.e. Shopee's anti-bot wall stalling the render, the
            # same failure CaptchaBlockedError exists for. Route it through
            # that retry path (fresh context, rotated proxy, backoff) instead
            # of failing immediately and non-retryably.
            raise CaptchaBlockedError(
                f"Web Unlocker API request timed out ({type(exc).__name__}) — likely Shopee's anti-bot wall "
                "stalling the render rather than a real connectivity failure"
            ) from exc
        except httpx.HTTPError as exc:
            # httpx's own connect/protocol exceptions frequently carry no
            # message (str(exc) == ""), which used to produce an unhelpful
            # "Web Unlocker API request failed: " with nothing after the
            # colon — fall back to the exception's class name so the failure
            # mode (ConnectError vs ...) is still visible.
            detail = str(exc) or type(exc).__name__
            raise ScraperError(f"Web Unlocker API request failed: {detail}") from exc

        # Bright Data reports a failed proxy leg (auth, suspended account,
        # target unreachable, ...) as HTTP 200 with an empty body and the
        # actual error only in this header — raise_for_status() above can't
        # see it, so an unchecked empty body would otherwise silently parse
        # as a "fallback" scrape result instead of a clear failure.
        brd_error = resp.headers.get("x-brd-error")
        if brd_error:
            if "waiting for selector" in brd_error.lower():
                # Same anti-bot-wall-stall signature as the TimeoutException
                # branch above, just observed within our own timeout window
                # instead of past it — same retry treatment.
                raise CaptchaBlockedError(f"Shopee showed a verification/anti-bot wall: {brd_error}")
            raise ScraperError(f"Web Unlocker API request failed: {brd_error}")

        html = resp.text

        if is_captcha_html(html):
            raise CaptchaBlockedError("Shopee showed a verification/anti-bot wall")

        # Check ld+json first — it's authoritative for price/rating (unlike
        # bff_data's item below, whose price/stock/rating Shopee nulls out on
        # this transport regardless of item validity), so a real product page
        # never falls through to the not-found check below. bff_data, when
        # also present in the same response, is Shopee's actual internal PDP
        # BFF payload (same {item, account, product_price, product_images,
        # shop_detailed, installment_drawer, product_description, ...} shape
        # the browser transport captures live from pdp/get_pc) — far richer
        # than ld+json's schema.org subset, so it's used as `raw` instead of
        # ld+json's own thinner dict whenever available; price/rating/
        # currency on the returned PDPData still come from ld+json either
        # way (_parse_ld_json_product backfills bff_data['item']'s own price/
        # item_rating from them too, since those are the two fields this
        # transport nulls out).
        product = self._extract_ld_json_product(html)
        if product is not None:
            bff_data = self._extract_pdp_bff_data(html, url)
            return self._parse_ld_json_product(product, url, raw_override=bff_data)

        pdp_fetch_error = _PDP_FETCH_ERROR.search(html)
        if pdp_fetch_error:
            # Confirmed dead/invalid item_id or shop_id (error code
            # 266900002 confirmed repeatedly against real dead URLs), not a
            # scrape failure — same "site confirmed it doesn't exist"
            # treatment as not_found_signature below, so this counts as a
            # successful scrape with no data rather than dragging down the
            # failure rate for a URL that was never going to resolve.
            raise ProductNotFoundError(
                f"Shopee's own PDP data fetch failed for this item (error code {pdp_fetch_error.group(1)}) "
                "— dead/invalid item_id or shop_id in the URL"
            )

        bff_data = self._extract_pdp_bff_data(html, url)
        if bff_data is not None:
            return self._parse_api_body({"data": bff_data}, url)

        if self.not_found_signature and self.not_found_signature in strip_script_and_style(html).lower():
            raise ProductNotFoundError("Shopee reports this product does not exist")

        return self._parse_html_fallback(html, url)

    async def _dataset_api_fetch(self, url: str) -> PDPData:
        """Shared implementation for BaseScraper.fetch_pdp_via_dataset_api —
        not exposed directly on ShopeeScraper (BR/VN still raise
        NotImplementedError via the base class default); a country adapter
        opts in by overriding fetch_pdp_via_dataset_api to call this.

        Runs Bright Data's maintained Shopee scraper (a Dataset API "scraper"
        product, separate from Web Unlocker/Scraping Browser) via its async
        trigger -> poll -> snapshot flow: Bright Data's own infrastructure
        handles anti-bot/CAPTCHA entirely server-side and hands back
        already-structured JSON (price included) instead of raw HTML, so this
        transport isn't subject to the risk-control rejection the browser and
        Web Unlocker transports hit on Shopee TH's live pdp/get_pc API.
        """
        url = _normalize_shopee_url(url)
        client = get_client()
        headers = {"Authorization": f"Bearer {settings.brightdata_api_token}", "Content-Type": "application/json"}

        try:
            trigger_resp = await client.post(
                _DATASET_TRIGGER_URL,
                params={"dataset_id": settings.brightdata_shopee_dataset_id},
                headers=headers,
                json={"input": [{"url": url}]},
            )
            trigger_resp.raise_for_status()
        except httpx.HTTPError as exc:
            detail = str(exc) or type(exc).__name__
            raise ScraperError(f"Bright Data dataset API trigger failed: {detail}") from exc

        snapshot_id = trigger_resp.json().get("snapshot_id")
        if not snapshot_id:
            raise ScraperError("Bright Data dataset API trigger response had no snapshot_id")

        status = None
        deadline = asyncio.get_event_loop().time() + settings.scrape_timeout_seconds
        while asyncio.get_event_loop().time() < deadline:
            try:
                progress_resp = await client.get(_DATASET_PROGRESS_URL.format(snapshot_id=snapshot_id), headers=headers)
                progress_resp.raise_for_status()
            except httpx.HTTPError as exc:
                detail = str(exc) or type(exc).__name__
                raise ScraperError(f"Bright Data dataset API progress check failed: {detail}") from exc

            status = progress_resp.json().get("status")
            if status == "ready":
                break
            if status == "failed":
                raise ScraperError(f"Bright Data dataset API job failed for snapshot {snapshot_id}")
            await asyncio.sleep(_DATASET_POLL_INTERVAL_SECONDS)
        else:
            raise ScraperError(
                f"Bright Data dataset API job did not complete within {settings.scrape_timeout_seconds}s "
                f"(last status: {status})"
            )

        try:
            snapshot_resp = await client.get(
                _DATASET_SNAPSHOT_URL.format(snapshot_id=snapshot_id), headers=headers, params={"format": "json"}
            )
            snapshot_resp.raise_for_status()
        except httpx.HTTPError as exc:
            detail = str(exc) or type(exc).__name__
            raise ScraperError(f"Bright Data dataset API snapshot fetch failed: {detail}") from exc

        records = snapshot_resp.json()
        if not records:
            raise ProductNotFoundError("Bright Data dataset API returned no records for this item")
        return self._parse_dataset_record(records[0], url)

    def _parse_dataset_record(self, record: dict, url: str) -> PDPData:
        # Field names confirmed against Bright Data's own published Shopee
        # dataset sample (luminati-io/Shopee-dataset-samples): final_price is
        # the actual current/discounted price, initial_price the pre-discount
        # one; image comes back as a JSON-array-encoded string, not a list.
        price_raw = record.get("final_price") or record.get("initial_price")
        try:
            price = float(price_raw) if price_raw not in (None, "") else None
        except (TypeError, ValueError):
            price = None

        rating_raw = record.get("rating")
        try:
            rating = float(rating_raw) if rating_raw not in (None, "") else None
        except (TypeError, ValueError):
            rating = None

        sold_raw = record.get("sold")
        try:
            sold = int(sold_raw) if sold_raw not in (None, "") else None
        except (TypeError, ValueError):
            sold = None

        images = record.get("image")
        if isinstance(images, str):
            try:
                images = json.loads(images)
            except json.JSONDecodeError:
                images = [images]
        if not isinstance(images, list):
            images = []

        match = _URL_ID_PATTERN.search(url)
        external_id = f"{match.group(1)}.{match.group(2)}" if match else (record.get("id") and str(record["id"]))

        return PDPData(
            site_key=self.site_key,
            product_url=url,
            external_product_id=external_id,
            title=record.get("title"),
            price=price,
            currency=record.get("currency") or self.default_currency,
            rating=rating,
            sold_count=sold,
            image_urls=[i for i in images if isinstance(i, str)],
            raw=record,
        )

    async def _apify_fetch(self, url: str) -> PDPData:
        """Shared implementation for BaseScraper.fetch_pdp_via_apify — not
        exposed directly on ShopeeScraper; a country adapter opts in by
        overriding fetch_pdp_via_apify to call this.

        Runs a third-party Apify actor (default: gio21/shopee-product-detail,
        confirmed to accept a direct product URL and cover shopee.co.th) via
        its synchronous run-sync-get-dataset-items endpoint — one HTTP call,
        no polling. The actor's own infrastructure handles Shopee's anti-bot
        layer entirely server-side, so this transport isn't subject to the
        risk-control rejection the browser transport hits — added as an
        alternative after every automated browser-driven approach (local
        headless/headed, Bright Data's Scraping Browser) still ran into some
        form of that wall. Pay-per-successful-result on Apify's side; a
        failed/blocked URL isn't billed, per their pricing page.

        Passing `country` explicitly (rather than relying on the actor's
        own URL-based auto-detection) turned out to matter a lot in
        practice: confirmed live that a shopee.vn URL with no `country` hung
        past this call's own 320s timeout with zero response, while the
        identical URL with `country: "VN"` resolved normally. unlocker_country
        (e.g. "th", "vn") already matches the actor's expected two-letter
        codes, so it costs nothing to always send it.
        """
        url = _normalize_shopee_url(url)
        actor = settings.apify_shopee_product_detail_actor_id.replace("/", "~")
        payload: dict = {"productUrls": [url]}
        if self.unlocker_country:
            payload["country"] = self.unlocker_country.upper()
        try:
            resp = await get_client().post(
                _APIFY_RUN_SYNC_URL.format(actor=actor),
                params={"token": settings.apify_api_token},
                json=payload,
                timeout=_APIFY_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            detail = str(exc) or type(exc).__name__
            raise ScraperError(f"Apify actor request failed: {detail}") from exc

        records = resp.json()
        if not records:
            raise ProductNotFoundError("Apify actor returned no records for this item")
        return self._parse_apify_record(records[0], url)

    def _parse_apify_record(self, record: dict, url: str) -> PDPData:
        # Field names per gio21/shopee-product-detail's documented output —
        # re-inspect and adjust here if a different Apify actor is
        # configured (APIFY_SHOPEE_PRODUCT_DETAIL_ACTOR_ID) or its schema
        # changes.
        price_raw = record.get("price")
        try:
            price = float(price_raw) if price_raw not in (None, "") else None
        except (TypeError, ValueError):
            price = None

        rating_raw = record.get("rating")
        try:
            rating = float(rating_raw) if rating_raw not in (None, "") else None
        except (TypeError, ValueError):
            rating = None

        # historicalSold is almost always null (Shopee redacts it) —
        # historicalSoldEstimated is a bucketed string like "10k+"/"5k+", not
        # a real count, so there's no clean numeric sold_count from this
        # actor; leave it unset rather than parse a misleading approximation.
        images = record.get("images")
        if not isinstance(images, list):
            images = []

        match = _URL_ID_PATTERN.search(url)
        external_id = f"{match.group(1)}.{match.group(2)}" if match else (record.get("itemId") and str(record["itemId"]))

        return PDPData(
            site_key=self.site_key,
            product_url=url,
            external_product_id=external_id,
            title=record.get("name"),
            price=price,
            currency=record.get("currency") or self.default_currency,
            rating=rating,
            sold_count=None,
            image_urls=[i for i in images if isinstance(i, str)],
            raw=record,
        )

    async def _apify_fetch_xtracto(self, url: str) -> PDPData:
        """Alternate Apify actor (xtracto/shopee-scraper), used for
        shopee_vn instead of _apify_fetch's gio21/shopee-product-detail —
        confirmed live that gio21 consistently falls back to its weaker
        "embedded_html" source for shopee.vn (real data but price/rating
        nulled), while this actor returned a real price/rating/shop object
        for the same country on a valid listing. Different input/output
        shape entirely (mode/url/fetchDetail vs productUrls array; shop_id
        +item_id/price_min/rating_star vs itemId/price/rating), so this is
        a parallel implementation rather than a shared one. A dead/invalid
        listing comes back as an all-null record (confirmed live: empty
        title, every field null) rather than an empty dataset or an error —
        detect that and raise ProductNotFoundError instead of returning a
        near-empty "success".
        """
        url = _normalize_shopee_url(url)
        actor = settings.apify_xtracto_shopee_actor_id.replace("/", "~")
        payload: dict = {"mode": "url", "url": url, "fetchDetail": True, "maxProducts": 1}
        if self.unlocker_country:
            payload["country"] = self.unlocker_country.lower()
        try:
            resp = await get_client().post(
                _APIFY_RUN_SYNC_URL.format(actor=actor),
                params={"token": settings.apify_api_token},
                json=payload,
                timeout=_APIFY_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            detail = str(exc) or type(exc).__name__
            raise ScraperError(f"Apify actor request failed: {detail}") from exc

        records = resp.json()
        if not records:
            raise ProductNotFoundError("Apify actor returned no records for this item")
        return self._parse_apify_xtracto_record(records[0], url)

    def _parse_apify_xtracto_record(self, record: dict, url: str) -> PDPData:
        if not record.get("title") and record.get("price") is None and not record.get("images"):
            raise ProductNotFoundError("Apify actor returned an all-null record — likely a dead/invalid item")

        price_raw = record.get("price") if record.get("price") not in (None, "") else record.get("price_min")
        try:
            price = float(price_raw) if price_raw not in (None, "") else None
        except (TypeError, ValueError):
            price = None

        rating_raw = record.get("rating_star")
        try:
            rating = float(rating_raw) if rating_raw not in (None, "") else None
        except (TypeError, ValueError):
            rating = None

        sold_raw = record.get("historical_sold") or record.get("sold")
        try:
            sold = int(sold_raw) if sold_raw not in (None, "") else None
        except (TypeError, ValueError):
            sold = None

        images = record.get("images")
        if not isinstance(images, list):
            images = []

        match = _URL_ID_PATTERN.search(url)
        if match:
            external_id = f"{match.group(1)}.{match.group(2)}"
        elif record.get("shop_id") is not None and record.get("item_id") is not None:
            external_id = f"{record['shop_id']}.{record['item_id']}"
        else:
            external_id = None

        return PDPData(
            site_key=self.site_key,
            product_url=url,
            external_product_id=external_id,
            title=record.get("title") or None,
            price=price,
            currency=record.get("currency") or self.default_currency,
            rating=rating,
            sold_count=sold,
            image_urls=[i for i in images if isinstance(i, str)],
            raw=record,
        )

    def _extract_pdp_bff_data(self, html: str, url: str) -> dict | None:
        """Returns the full cached PDP BFF payload — {item, account,
        product_images, product_price, product_review, shop_detailed,
        installment_drawer, product_description, ...} — not just the `item`
        sub-object, so callers get the same breadth of data a direct capture
        of the live pdp/get_pc response would have.
        """
        match = _URL_ID_PATTERN.search(url)
        cache_key = f"{match.group(1)}.{match.group(2)}" if match else None

        for block in _MFE_INITIAL_DATA_BLOCK.finditer(html):
            if '"PDP_BFF_DATA"' not in block.group(1):
                continue
            try:
                data = json.loads(block.group(1))
            except json.JSONDecodeError:
                continue
            try:
                cached_map = data["initialState"]["DOMAIN_PDP"]["data"]["PDP_BFF_DATA"]["cachedMap"]
            except (KeyError, TypeError):
                continue
            if not cached_map:
                continue
            # Cache key format confirmed as "<shop_id>/<item_id>" (note: "/",
            # not the "." used in our own external_product_id) — fall back to
            # the sole entry if the URL's ids don't match any key (defensive;
            # not observed in testing).
            entry = cached_map.get(cache_key.replace(".", "/")) if cache_key else None
            if entry is None and len(cached_map) == 1:
                entry = next(iter(cached_map.values()))
            if entry and entry.get("item"):
                return entry
        return None

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

    def _parse_ld_json_product(self, item: dict, url: str, raw_override: dict | None = None) -> PDPData:
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

        raw = raw_override if raw_override is not None else item
        if raw_override is not None:
            # bff_data (raw_override) is the full PDP BFF payload — {item,
            # account, product_price, product_images, shop_detailed, ...} —
            # but its item.price/item.item_rating are nulled out on this
            # transport regardless of item validity (see the comment above
            # this method's call site). ld+json is authoritative for both and
            # we've already computed them, so backfill rather than leave two
            # of the most-used fields empty on the one sub-object callers
            # actually reach for. Keep price in Shopee's native item.price
            # scale (currency units * 100000, same divisor _parse_api_body
            # uses) so raw stays internally consistent no matter which
            # transport filled it in.
            inner_item = raw.get("item") if isinstance(raw.get("item"), dict) else raw
            if inner_item.get("price") is None and price is not None:
                inner_item["price"] = round(price * 100000)
            existing_rating = inner_item.get("item_rating")
            if rating is not None and (not isinstance(existing_rating, dict) or existing_rating.get("rating_star") is None):
                inner_item["item_rating"] = {**(existing_rating or {}), "rating_star": rating}

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
            raw=raw,
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
        data = body.get("data") or {}
        item = data.get("item") or data
        if not item:
            if "error" in body:
                # Shopee's risk-control layer can reject the live pdp/get_pc
                # call with HTTP 200 and a minified error body instead of a
                # captcha page — confirmed via live testing against shopee_th
                # (body shaped like {"error": 90309999, ...}, no "data" key at
                # all) even with an authenticated session. Same anti-bot-wall
                # shape CaptchaBlockedError exists for; route it through that
                # retry path (fresh context, rotated proxy, backoff) instead
                # of failing immediately and non-retryably as a plain
                # ScraperError would.
                raise CaptchaBlockedError(
                    f"Shopee's PDP API rejected this request (error code {body.get('error')}) — "
                    "likely risk-control rather than a real data gap"
                )
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
            # Preserve the full PDP BFF payload (item + account +
            # product_price + product_images + shop_detailed +
            # installment_drawer + product_description + ...) whenever body
            # actually nests it that way — same shape Shopee's frontend gets
            # from a direct pdp/get_pc call — rather than just the item
            # subset; only falls back to item-only when data.item wasn't
            # nested to begin with (e.g. a flatter body some other caller
            # constructed).
            raw=data if data.get("item") is not None else item,
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
