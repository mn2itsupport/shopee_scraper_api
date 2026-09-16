"""Shopee Malaysia PDP adapter. Shared logic lives in _shopee_common.py —
this file only pins the country-specific constants."""

from app.config import settings
from app.models.schemas import PDPData
from app.scrapers.sites._shopee_common import ShopeeScraper


class ShopeeMYScraper(ShopeeScraper):
    site_key = "shopee_my"
    base_domain = "shopee.com.my"
    default_currency = "MYR"
    locale = "ms-MY"
    timezone_id = "Asia/Kuala_Lumpur"
    geolocation = {"latitude": 3.1390, "longitude": 101.6869}
    unlocker_country = "my"
    # Not yet confirmed against a real dead shopee.com.my product page —
    # verify and correct if it ever produces a false positive (see
    # shopee_vn.py's same caveat).
    not_found_signature = "produk tidak wujud"

    # See ShopeeTHScraper.browser_mode_override — same escape hatch, empty
    # by default so shopee_my stays on the global BROWSER_MODE until
    # SHOPEE_MY_BROWSER_MODE_OVERRIDE is explicitly set.
    @property
    def browser_mode_override(self) -> str:
        return settings.shopee_my_browser_mode_override

    async def fetch_pdp_via_apify(self, url: str) -> PDPData:
        # Same xtracto actor shopee_vn uses — not yet independently confirmed
        # to cover shopee.com.my; verify before relying on it in production.
        return await self._apify_fetch_xtracto(url)
