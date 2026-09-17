"""Shopee Thailand PDP adapter. Shared logic lives in _shopee_common.py — this
file only pins the country-specific constants."""

import asyncio
import logging
import re

from app.config import settings
from app.models.schemas import PDPData
from app.scrapers.base import ScraperError
from app.scrapers.sites._shopee_common import ShopeeScraper

logger = logging.getLogger(__name__)

# curl_cffi's embedded mfe-initial-data snapshot duplicates a handful of
# item/model fields under their legacy pre-get_pc names (itemid/shopid/name/
# images/cod_flag, models[].itemid/modelid/promotionid) alongside the
# current get_pc-named fields (item_id/shop_id/title/image,
# models[].item_id/model_id/promotion_id) — confirmed live (2026-09-15)
# these always carry the same value as their current-named counterpart, so
# they're pure legacy cruft rather than distinct data. The rest of this set
# is the same problem one level removed: a real pdp/get_pc capture
# (ThaiResponse.txt) only ever has these as fields on a *different* `data`
# sibling — age_gate/coin_info on `data` itself (already preserved verbatim
# there — see fetch_pdp_via_curl_cffi), cmt_count/liked/liked_count/
# historical_sold/global_sold/should_move_ratings_above on `product_review`,
# show_best_price_guarantee/show_official_shop_label_in_title/
# show_original_guarantee/show_shopee_verified_label on `product_meta`,
# is_cc_installment_payment_eligible/is_non_cc_installment_payment_eligible
# on `promotion_info.item_installment_eligibility`, shop_vouchers as its own
# top-level sibling, long_images/video_info_list on `product_images` — but
# curl_cffi's snapshot also nests a duplicate copy directly under item, with
# the same value (confirmed live 2026-09-15). credit_insurance_data/
# coin_earn_label/has_lowest_price_guarantee have no such counterpart
# anywhere and don't appear in a real capture at all. Stripped so a
# shopee_th response looks like a real pdp/get_pc capture's shape
# regardless of transport — dict comprehensions below only ever filter,
# never reorder, so every remaining key keeps its original relative
# position.
_CURL_CFFI_LEGACY_ITEM_KEYS = frozenset(
    {
        "itemid", "shopid", "name", "images", "cod_flag", "age_gate", "coin_info", "credit_insurance_data",
        "cmt_count", "coin_earn_label", "global_sold", "has_lowest_price_guarantee", "historical_sold",
        "is_cc_installment_payment_eligible", "is_non_cc_installment_payment_eligible", "liked", "liked_count",
        "long_images", "shop_vouchers", "should_move_ratings_above", "show_best_price_guarantee",
        "show_official_shop_label_in_title", "show_original_guarantee", "show_shopee_verified_label",
        "video_info_list",
    }
)
_CURL_CFFI_LEGACY_MODEL_KEYS = frozenset({"itemid", "modelid", "promotionid"})
# item.item_rating nests rating_count/total_rating_count too — same
# duplicate-of-product_review problem as the item-level keys above, but one
# level deeper, so it needs its own filter rather than a flat key match.
_CURL_CFFI_LEGACY_ITEM_RATING_KEYS = frozenset({"rating_count", "total_rating_count"})
# data.event_type and data.product_attributes.fresh_featured_attrs: no
# get_pc counterpart anywhere, confirmed absent from ThaiResponse.txt.
_CURL_CFFI_LEGACY_DATA_KEYS = frozenset({"event_type"})


# Price fields on a live pdp/get_pc capture are Shopee's internal integer
# scale (currency units * 100000, same divisor _parse_api_body uses) — the
# xtracto Apify actor already normalizes these to plain currency units
# (e.g. 28, not 2800000), so they need re-scaling to look like a native
# capture rather than a plain float.
_XTRACTO_PRICE_FIELDS = ("price", "price_min", "price_max", "price_before_discount", "price_min_before_discount", "price_max_before_discount")
_CDN_IMAGE_ID = re.compile(r"/file/([^/?#]+)")

# Fields _fetch_price_patch_via_browser lifts off a live pdp/get_pc capture's
# item object to patch onto curl_cffi's response — a subset of
# _GET_PC_ITEM_KEY_ORDER, deliberately scoped to just what curl_cffi's own
# transport can't see (price/stock/rating/sold), not the whole item, so a
# successful probe only ever fills gaps rather than overwriting fields
# curl_cffi already got right (title, images, description, ...). Already in
# get_pc's native integer scale (price * 100000) — no rescaling needed here,
# unlike _xtracto_record_to_get_pc_raw's actor-sourced values.
_PRICE_PATCH_ITEM_FIELDS = (*_XTRACTO_PRICE_FIELDS, "stock", "historical_sold", "sold", "item_rating")


def _apply_price_patch(raw: dict, patch: dict) -> dict:
    data = raw.get("data")
    item = data.get("item") if isinstance(data, dict) else None
    if not isinstance(item, dict):
        return raw
    return {**raw, "data": {**data, "item": {**item, **patch}}}


# item's own key sequence on a real live pdp/get_pc capture — confirmed
# against a real shopee_th response (see ThaiResponse.txt). xtracto's own
# record shape doesn't share this order (it's the actor's own field order),
# so _xtracto_record_to_get_pc_raw's item dict is rebuilt in this sequence
# rather than left as whatever order dict(record) happened to produce, to
# look identical to a native capture regardless of which fields the actor
# actually populated. Any field the actor returns that isn't in this list
# (actor-only, not part of get_pc's own shape) is appended after, in
# whatever order it was already in.
_GET_PC_ITEM_KEY_ORDER = (
    "item_id", "shop_id", "item_status", "status", "item_type", "reference_item_id",
    "title", "image", "label_ids", "is_adult", "is_preview", "flag",
    "is_service_by_shopee", "condition", "cat_id", "has_low_fulfillment_rate",
    "is_live_streaming_price", "currency", "brand", "brand_id", "show_discount",
    "ctime", "item_rating", "cb_option", "has_model_with_available_shopee_stock",
    "shop_location", "attributes", "rich_text_description", "invoice_option",
    "is_category_failed", "is_prescription_item", "preview_info",
    "show_prescription_feed", "is_alcohol_product", "is_infant_milk_formula_product",
    "is_unavailable", "is_partial_fulfilled", "is_presale", "is_presale_deposit_item",
    "is_presale_deposit_made", "description", "categories", "fe_categories",
    "item_has_video", "presale_dday_start_time", "is_lowest_price_at_shopee",
    "display_description_disclosure_rsku_redirection", "display_similar_sold",
    "title_type", "authorized_brand_name", "is_sexual", "models", "tier_variations",
    "size_chart", "size_chart_info", "welcome_package_type", "is_free_gift",
    "deep_discount", "is_low_price_eligible", "bundle_deal_info", "add_on_deal_info",
    "shipping_icon_type", "badge_icon_type", "spl_info", "estimated_days",
    "is_pre_order", "is_free_shipping", "overall_purchase_limit", "min_purchase_limit",
    "is_hide_stock", "stock", "normal_stock", "current_promotion_reserved_stock",
    "can_use_wholesale", "wholesale_tier_list", "price", "raw_discount",
    "hidden_price_display", "price_min", "price_max", "price_before_discount",
    "price_min_before_discount", "price_max_before_discount", "other_stock",
    "discount_stock", "current_promotion_has_reserve_stock", "complaint_policy",
    "show_recycling_info", "should_show_amp_tag", "all_models_has_pre_order",
    "is_item_inherited", "max_quantity", "drug_details", "selected_real_models",
    "size_tier_variation_idx", "is_fashion_item", "social_proof_label", "title_tr",
    "description_tr", "rich_text_description_tr", "stock_display",
    "max_quantity_display", "disclaimer",
)


def _reorder_like(d: dict, key_order: tuple[str, ...]) -> dict:
    ordered = {k: d[k] for k in key_order if k in d}
    ordered.update({k: v for k, v in d.items() if k not in ordered})
    return ordered


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
    item = _reorder_like(item, _GET_PC_ITEM_KEY_ORDER)

    return {"bff_meta": None, "error": None, "error_msg": None, "data": {"item": item}}


def _dedupe_curl_cffi_legacy_keys(raw: dict) -> dict:
    data = raw.get("data")
    if not isinstance(data, dict):
        return raw
    deduped_data = {k: v for k, v in data.items() if k not in _CURL_CFFI_LEGACY_DATA_KEYS}

    product_attributes = deduped_data.get("product_attributes")
    if isinstance(product_attributes, dict):
        deduped_data["product_attributes"] = {k: v for k, v in product_attributes.items() if k != "fresh_featured_attrs"}

    item = deduped_data.get("item")
    if not isinstance(item, dict):
        return {**raw, "data": deduped_data}

    deduped_item = {k: v for k, v in item.items() if k not in _CURL_CFFI_LEGACY_ITEM_KEYS}

    item_rating = deduped_item.get("item_rating")
    if isinstance(item_rating, dict):
        deduped_item["item_rating"] = {k: v for k, v in item_rating.items() if k not in _CURL_CFFI_LEGACY_ITEM_RATING_KEYS}

    models = deduped_item.get("models")
    if isinstance(models, list):
        deduped_item["models"] = [
            {k: v for k, v in model.items() if k not in _CURL_CFFI_LEGACY_MODEL_KEYS} if isinstance(model, dict) else model
            for model in models
        ]

    deduped_data["item"] = deduped_item
    return {**raw, "data": deduped_data}


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
        #
        # Widened from ProductNotFoundError to ScraperError (its parent —
        # catches both) after confirming live (2026-09-15) that the Apify
        # account can hit a hard account-level failure (403 "Monthly usage
        # hard limit exceeded", plus an observed 400) that isn't the actor's
        # own per-item not-found signal at all — _apify_fetch_xtracto raises
        # that as ScraperError, which used to propagate straight out of this
        # method and fail every shopee_th request outright while the Apify
        # account is capped. Same fallback either way: curl_cffi can't see
        # price/rating/sold either, but still recovers real title/images/
        # description instead of the request failing completely.
        try:
            pdp = await self._apify_fetch_xtracto(url)
        except ScraperError:
            return await self.fetch_pdp_via_curl_cffi(url)
        return pdp.model_copy(update={"raw": _xtracto_record_to_get_pc_raw(pdp.raw)})

    async def fetch_pdp_via_curl_cffi(self, url: str) -> PDPData:
        patch: dict | None = None
        if settings.shopee_th_price_probe_enabled:
            pdp, patch = await asyncio.gather(
                self._curl_cffi_fetch(url),
                self._fetch_price_patch_via_browser(url),
            )
            if patch:
                logger.info("shopee_th price probe recovered %d field(s) for %s", len(patch), url)
            else:
                logger.info("shopee_th price probe found no usable price data for %s", url)
        else:
            pdp = await self._curl_cffi_fetch(url)

        raw = _dedupe_curl_cffi_legacy_keys(pdp.raw)
        if not (patch and settings.shopee_th_price_probe_merge):
            return pdp.model_copy(update={"raw": raw})

        # Shadow mode off (shopee_th_price_probe_merge=True): patch the
        # deduped item in place and recompute the same top-level fields
        # _parse_api_body derives from a live capture, so PDPData.price/
        # rating/sold_count actually reflect the probe's data instead of
        # curl_cffi's nulls once this is trusted.
        raw = _apply_price_patch(raw, patch)
        item = raw["data"]["item"]
        price_raw = item.get("price") or item.get("price_min")
        price = price_raw / 100000 if isinstance(price_raw, (int, float)) else pdp.price
        rating = (item.get("item_rating") or {}).get("rating_star") or pdp.rating
        sold_count = item.get("historical_sold") or item.get("sold") or pdp.sold_count
        return pdp.model_copy(update={"raw": raw, "price": price, "rating": rating, "sold_count": sold_count})

    async def _fetch_price_patch_via_browser(self, url: str) -> dict | None:
        """Best-effort side probe: opens the persistent-profile browser
        context (the same one browser_mode_override="local" would use) and
        intercepts the live pdp/get_pc XHR, concurrently with the curl_cffi
        call above, purely to measure whether it can recover price/rating/
        sold data curl_cffi's own transport can't see. Never raises — every
        failure mode (anti-bot wall, decode error, our own timeout) is
        logged and swallowed here so this stays pure enrichment, never a
        dependency the main response can fail on. See
        settings.shopee_th_price_probe_enabled/_timeout_seconds/_merge.

        Imports browser_pool lazily: browser_pool imports ShopeeTHScraper at
        module load time (to key its persistent-profile context off
        site_key == "shopee_th"), so importing it back at this module's top
        level would be circular.
        """
        from app.scrapers.browser_pool import acquire_context

        try:
            async with acquire_context(
                self.site_key, self.locale, self.timezone_id, self.geolocation, self.unlocker_country
            ) as context:
                pdp = await asyncio.wait_for(
                    self.fetch_pdp(context, url),
                    timeout=settings.shopee_th_price_probe_timeout_seconds,
                )
        except Exception:
            logger.info("shopee_th price probe failed for %s", url, exc_info=True)
            return None

        item = (pdp.raw.get("data") or {}).get("item")
        if not isinstance(item, dict):
            return None

        patch = {k: item[k] for k in _PRICE_PATCH_ITEM_FIELDS if k in item and item[k] is not None}
        return patch or None
