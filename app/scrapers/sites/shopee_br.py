"""Shopee Brazil PDP adapter. Shared logic lives in _shopee_common.py — this
file only pins the country-specific constants."""

from app.config import settings
from app.models.schemas import PDPData
from app.scrapers.sites._shopee_common import ShopeeScraper


class ShopeeBRScraper(ShopeeScraper):
    site_key = "shopee_br"
    base_domain = "shopee.com.br"
    default_currency = "BRL"
    locale = "pt-BR"
    timezone_id = "America/Sao_Paulo"
    geolocation = {"latitude": -23.5505, "longitude": -46.6333}
    unlocker_country = "br"
    not_found_signature = "o produto não existe"

    # See ShopeeTHScraper.browser_mode_override — same escape hatch.
    # "local"/"brightdata_cdp" both route through the browser-driven live
    # pdp/get_pc capture (ShopeeScraper.fetch_pdp) but got CaptchaBlockedError
    # on every attempt against shopee_br (confirmed live 2026-09-10 via
    # scripts/shopee_br_local_mode_pilot.py / shopee_br_cdp_mode_pilot.py) —
    # same anti-bot wall shopee_th/vn hit. "apify" (the current
    # SHOPEE_BR_BROWSER_MODE_OVERRIDE) avoids that fight entirely, same as
    # shopee_th/vn — confirmed live returning real price/rating/shop data
    # (scripts/shopee_br_apify_mode_pilot.py), just under the actor's own
    # schema rather than Shopee's native get_pc key names.
    @property
    def browser_mode_override(self) -> str:
        return settings.shopee_br_browser_mode_override

    async def fetch_pdp_via_dataset_api(self, url: str) -> PDPData:
        return await self._dataset_api_fetch(url)

    async def fetch_pdp_via_apify(self, url: str) -> PDPData:
        return await self._apify_fetch(url)
