"""Shopee Singapore PDP adapter. Shared logic lives in _shopee_common.py —
this file only pins the country-specific constants."""

from app.config import settings
from app.models.schemas import PDPData
from app.scrapers.sites._shopee_common import ShopeeScraper


class ShopeeSGScraper(ShopeeScraper):
    site_key = "shopee_sg"
    base_domain = "shopee.sg"
    default_currency = "SGD"
    locale = "en-SG"
    timezone_id = "Asia/Singapore"
    geolocation = {"latitude": 1.3521, "longitude": 103.8198}
    unlocker_country = "sg"
    # Not yet confirmed against a real dead shopee.sg product page — verify
    # and correct if it ever produces a false positive (see shopee_vn.py's
    # same caveat).
    not_found_signature = "page not found"

    # See ShopeeTHScraper.browser_mode_override — same escape hatch, empty
    # by default so shopee_sg stays on the global BROWSER_MODE until
    # SHOPEE_SG_BROWSER_MODE_OVERRIDE is explicitly set.
    @property
    def browser_mode_override(self) -> str:
        return settings.shopee_sg_browser_mode_override

    async def fetch_pdp_via_apify(self, url: str) -> PDPData:
        # Same xtracto actor shopee_vn uses — not yet independently confirmed
        # to cover shopee.sg; verify before relying on it in production.
        return await self._apify_fetch_xtracto(url)
