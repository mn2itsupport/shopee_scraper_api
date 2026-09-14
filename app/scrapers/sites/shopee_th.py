"""Shopee Thailand PDP adapter. Shared logic lives in _shopee_common.py — this
file only pins the country-specific constants."""

import re

from app.config import settings
from app.models.schemas import PDPData
from app.scrapers.base import ProductNotFoundError
from app.scrapers.sites._shopee_common import ShopeeScraper

# Shopee's own UI/experiment plumbing — feature-flag toggles, banner/upsell
# placements, none of it real product data — present in the payload
# _curl_cffi_fetch returns (same underlying cached PDP BFF shape
# _extract_pdp_bff_data always returns) alongside item/price/images/etc.
# Stripped only from THIS transport's response (see fetch_pdp_via_curl_cffi
# below), by explicit request — every other Shopee site/transport still
# returns _parse_api_body's/_parse_pdp_page_html's output verbatim.
_CURL_CFFI_NOISE_KEYS_DATA = frozenset(
    {
        "design_control",
        "coin_info",
        "age_gate",
        "membership_exclusive",
        "membership_exclusive_teaser",
        "ongoing_banner",
        "teaser_banner",
        "button_group",
        "service_entrance",
        "service_drawer",
        "removed_fields",
        "product_meta",
    }
)
_CURL_CFFI_NOISE_KEYS_ITEM = frozenset(
    {
        "credit_insurance_data",
        "age_gate",
        "coin_info",
        "size_chart",
        "size_chart_info",
        "welcome_package_type",
        "spl_info",
        "disclaimer",
        "social_proof_label",
    }
)


# Price fields on a live pdp/get_pc capture are Shopee's internal integer
# scale (currency units * 100000, same divisor _parse_api_body uses) — the
# xtracto Apify actor already normalizes these to plain currency units
# (e.g. 28, not 2800000), so they need re-scaling to look like a native
# capture rather than a plain float.
_XTRACTO_PRICE_FIELDS = ("price", "price_min", "price_max", "price_before_discount", "price_min_before_discount", "price_max_before_discount")
_CDN_IMAGE_ID = re.compile(r"/file/([^/?#]+)")


def _xtracto_image_id(image_url: str) -> str:
    # Native item.images is a list of bare CDN file ids (e.g.
    # "sg-11134201-7rdx9-lytl6l218u441a"), not full URLs — _parse_api_body
    # rebuilds the URL itself as f"https://cf.{base_domain}/file/{img}". The
    # actor instead hands back full down-th.img.susercontent.com URLs for
    # the same ids, so strip everything but the id to match.
    match = _CDN_IMAGE_ID.search(image_url)
    return match.group(1) if match else image_url


def _xtracto_record_to_get_pc_raw(record: dict) -> dict:
    """Reshapes _apify_fetch_xtracto's already-close-to-native record (it
    already uses most of Shopee's own field names — item_id, shop_id,
    images, tier_variations, ...) into the exact envelope/field-naming a
    live pdp/get_pc capture produces, so a shopee_th response looks the same
    to a caller regardless of which transport actually served it.

    Deliberately scoped to the item object only — the fields _parse_api_body
    and PDPData actually read (name/price/currency/item_rating/
    historical_sold/images) plus every other field the actor already names
    natively, passed through as-is. get_pc's `data` also carries shop_detailed,
    product_price, installment_drawer, etc. as siblings of item; the actor's
    own "shop" sub-object doesn't share shop_detailed's real (unconfirmed,
    always-null-in-testing-so-far) field names, so it's left out rather than
    fabricate a schema never observed live.
    """
    item = dict(record)
    item["name"] = item.pop("title", None)

    for field in _XTRACTO_PRICE_FIELDS:
        raw_value = item.get(field)
        if isinstance(raw_value, (int, float)):
            item[field] = round(raw_value * 100000)

    images = item.get("images")
    if isinstance(images, list):
        item["images"] = [_xtracto_image_id(i) if isinstance(i, str) else i for i in images]

    item["item_rating"] = {
        "rating_star": item.pop("rating_star", None),
        "rating_count": item.pop("rating_count", None) or [],
        "total_rating_count": item.pop("total_ratings", None),
    }
    item.pop("shop", None)

    return {"error": None, "error_msg": None, "bff_meta": None, "data": {"item": item}}


def _trim_curl_cffi_raw(raw: dict) -> dict:
    data = raw.get("data")
    if not isinstance(data, dict):
        return raw
    trimmed_data = {k: v for k, v in data.items() if k not in _CURL_CFFI_NOISE_KEYS_DATA}
    item = trimmed_data.get("item")
    if isinstance(item, dict):
        trimmed_data["item"] = {k: v for k, v in item.items() if k not in _CURL_CFFI_NOISE_KEYS_ITEM}
    return {**raw, "data": trimmed_data}


class ShopeeTHScraper(ShopeeScraper):
    site_key = "shopee_th"
    base_domain = "shopee.co.th"
    default_currency = "THB"
    locale = "th-TH"
    timezone_id = "Asia/Bangkok"
    geolocation = {"latitude": 13.7563, "longitude": 100.5018}
    # Required in practice, not just a nice-to-have: without this, Bright
    # Data's Web Unlocker auto-picks an exit IP that Shopee TH's anti-bot
    # layer flags, and the request never resolves (still failing after
    # 90s+ waiting for the product selector to render) — confirmed by
    # testing the Unlocker API directly. Forcing "th" resolves in ~15s.
    unlocker_country = "th"
    # not_found_signature left unset (unverified localized copy) — a
    # nonexistent product still falls through to the HTML/DOM fallback
    # instead of raising ScraperError; fill in once confirmed against a real
    # dead product page, same as shopee_br.py's.

    # Every *anonymous* transport tried against Shopee TH (bare Playwright,
    # Bright Data Web Unlocker REST, Bright Data's own Scraping Browser) got
    # risk-control rejected on the live pdp/get_pc call, which is the only
    # source of real price/rating/sold data — the Unlocker transport's HTML
    # fallback (_extract_pdp_bff_data) gets those fields nulled out by Shopee
    # regardless of item validity. And every *authenticated* browser-driven
    # attempt (local headless/headed, Patchright, Bright Data's Scraping
    # Browser with a real human login) still ran into some form of the same
    # anti-bot wall on the very next automated action — confirmed via
    # extensive live testing, not just anonymous access. SHOPEE_TH_BROWSER_MODE_OVERRIDE
    # (settings.shopee_th_browser_mode_override) picks the transport for this
    # one site independent of every other site's BROWSER_MODE: "local" is
    # that persistent-profile + Patchright path; "apify" routes through a
    # third-party Apify actor instead (see _shopee_common.py's _apify_fetch),
    # which avoids the anti-bot fight entirely by running server-side, but is
    # gated by Apify's free-plan cache (a URL not already in their shared
    # cache returns an empty record instead of scraping it live); "curl_cffi"
    # (see _shopee_common.py's _curl_cffi_fetch) is a third option — no
    # browser, no third party, a plain HTTP client impersonating a real
    # Chrome TLS fingerprint — confirmed via live testing to get past the
    # same risk-control wall for the page load itself (title/images/
    # description recovered), just not for the internal pdp/get_pc call
    # specifically (still risk-control-rejected direct, same as every other
    # transport), so price/rating/sold_count stay null on this one too.
    @property
    def browser_mode_override(self) -> str:
        return settings.shopee_th_browser_mode_override

    async def fetch_pdp_via_dataset_api(self, url: str) -> PDPData:
        return await self._dataset_api_fetch(url)

    async def fetch_pdp_via_apify(self, url: str) -> PDPData:
        # xtracto/shopee-scraper instead of the base _apify_fetch's
        # gio21/shopee-product-detail — confirmed live (2026-09-14) that
        # xtracto returns a real price (28 THB) and rating (4.7) for a real
        # shopee_th product (gio21 not directly compared here, but it hits
        # the same free-plan-cache ceiling documented above regardless).
        # _xtracto_record_to_get_pc_raw reshapes its output to match a live
        # pdp/get_pc capture's envelope.
        #
        # Confirmed live (2026-09-15, 6-product sample) this actor's
        # coverage is inconsistent: 2 of 6 real, still-listed products came
        # back as _apify_fetch_xtracto's own all-null-record
        # ProductNotFoundError even though neither was actually dead —
        # that heuristic can't tell "dead item" from "actor doesn't have
        # this one cached/covered" apart. Since curl_cffi (a transport this
        # site already supports) can independently confirm real dead items
        # via its own not_found_signature/_PDP_FETCH_ERROR checks and, for a
        # live one, at least recovers real title/images/description instead
        # of nothing, fall back to it here rather than surface a possibly-
        # wrong not-found straight from the actor's own weak heuristic.
        try:
            pdp = await self._apify_fetch_xtracto(url)
        except ProductNotFoundError:
            return await self.fetch_pdp_via_curl_cffi(url)
        return pdp.model_copy(update={"raw": _xtracto_record_to_get_pc_raw(pdp.raw)})

    async def fetch_pdp_via_curl_cffi(self, url: str) -> PDPData:
        pdp = await self._curl_cffi_fetch(url)
        return pdp.model_copy(update={"raw": _trim_curl_cffi_raw(pdp.raw)})
