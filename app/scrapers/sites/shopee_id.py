"""Shopee Indonesia PDP adapter. Shared logic lives in _shopee_common.py —
this file only pins the country-specific constants."""

from app.config import settings
from app.models.schemas import PDPData
from app.scrapers.sites._shopee_common import ShopeeScraper


class ShopeeIDScraper(ShopeeScraper):
    site_key = "shopee_id"
    base_domain = "shopee.co.id"
    default_currency = "IDR"
    locale = "id-ID"
    timezone_id = "Asia/Jakarta"
    geolocation = {"latitude": -6.2088, "longitude": 106.8456}
    unlocker_country = "id"
    # not_found_signature left unset (unverified localized copy) — same as
    # shopee_sg.py/shopee_th.py. The generic _PDP_FETCH_ERROR check in
    # _parse_pdp_page_html should catch dead/invalid item_id or shop_id the
    # same way it's confirmed to for shopee_sg/my/ph/br — not yet
    # independently confirmed for shopee.co.id specifically since curl_cffi
    # can't yet reach it at all (see browser_mode_override comment below).

    # See ShopeeSGScraper's own comment for the full picture. UNLIKE
    # shopee_my/ph, curl_cffi against shopee.co.id still gets CONNECT tunnel
    # failed (400) via BRIGHTDATA_RESIDENTIAL_ZONE as of 2026-09-16 — that
    # zone's country-targeting needs "id" added too (same fix already
    # applied for "sg"; see proxy_provider.py's "-country-<cc>" suffix)
    # before this transport (or "local") can reach shopee.co.id at all.
    # Defaulting to curl_cffi anyway since it's the correct transport once
    # that's fixed — same trajectory shopee_sg went through.
    @property
    def browser_mode_override(self) -> str:
        return settings.shopee_id_browser_mode_override

    async def fetch_pdp_via_apify(self, url: str) -> PDPData:
        # Same xtracto actor shopee_vn uses — not yet independently confirmed
        # to cover shopee.co.id (blocked on a 403 from the Apify account
        # itself as of 2026-09-16, reproduced against shopee_vn too, so not
        # shopee_id-specific — retest once that account/token issue is fixed).
        return await self._apify_fetch_xtracto(url)

    async def fetch_pdp_via_curl_cffi(self, url: str) -> PDPData:
        return await self._curl_cffi_fetch(url)
