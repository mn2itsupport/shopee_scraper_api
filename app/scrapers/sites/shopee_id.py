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
    # shopee_sg.py/shopee_th.py. Not needed in practice: confirmed live
    # 2026-09-16 that a dead/invalid item_id or shop_id on shopee.co.id is
    # already caught generically by _parse_pdp_page_html's _PDP_FETCH_ERROR
    # check (error code 266900002, same ProductNotFoundError raw shape
    # {bff_meta, error, error_msg, data} shopee_sg/my/ph/br get via the same
    # shared tail).

    # See ShopeeSGScraper's own comment for the full picture — same
    # reasoning applies here. curl_cffi initially got CONNECT tunnel failed
    # (400) via BRIGHTDATA_RESIDENTIAL_ZONE until "id" was added to that
    # zone's country-targeting (same fix already applied for "sg"; see
    # proxy_provider.py's "-country-<cc>" suffix) — confirmed live
    # 2026-09-16, after that fix, reaching shopee.co.id cleanly and
    # correctly raising ProductNotFoundError for a dead item_id; not yet
    # independently confirmed recovering real title/price/images for a live
    # product on this domain specifically.
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
