"""Shopee Philippines PDP adapter. Shared logic lives in _shopee_common.py —
this file only pins the country-specific constants."""

from app.config import settings
from app.models.schemas import PDPData
from app.scrapers.sites._shopee_common import ShopeeScraper


class ShopeePHScraper(ShopeeScraper):
    site_key = "shopee_ph"
    base_domain = "shopee.ph"
    default_currency = "PHP"
    locale = "en-PH"
    timezone_id = "Asia/Manila"
    geolocation = {"latitude": 14.5995, "longitude": 120.9842}
    unlocker_country = "ph"
    # Not yet confirmed against a real dead shopee.ph product page — verify
    # and correct if it ever produces a false positive (see shopee_vn.py's
    # same caveat).
    not_found_signature = "product not found"

    # See ShopeeTHScraper.browser_mode_override — same escape hatch, empty
    # by default so shopee_ph stays on the global BROWSER_MODE until
    # SHOPEE_PH_BROWSER_MODE_OVERRIDE is explicitly set.
    @property
    def browser_mode_override(self) -> str:
        return settings.shopee_ph_browser_mode_override

    async def fetch_pdp_via_apify(self, url: str) -> PDPData:
        # Same xtracto actor shopee_vn uses — not yet independently confirmed
        # to cover shopee.ph; verify before relying on it in production.
        return await self._apify_fetch_xtracto(url)
