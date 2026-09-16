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
    # Not yet confirmed against a real dead shopee.co.id product page —
    # verify and correct if it ever produces a false positive (see
    # shopee_vn.py's same caveat).
    not_found_signature = "produk tidak ditemukan"

    # See ShopeeTHScraper.browser_mode_override — same escape hatch, empty
    # by default so shopee_id stays on the global BROWSER_MODE until
    # SHOPEE_ID_BROWSER_MODE_OVERRIDE is explicitly set.
    @property
    def browser_mode_override(self) -> str:
        return settings.shopee_id_browser_mode_override

    async def fetch_pdp_via_apify(self, url: str) -> PDPData:
        # Same xtracto actor shopee_vn uses — not yet independently confirmed
        # to cover shopee.co.id; verify before relying on it in production.
        return await self._apify_fetch_xtracto(url)
