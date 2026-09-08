"""Shopee Brazil PDP adapter. Shared logic lives in _shopee_common.py — this
file only pins the country-specific constants."""

from app.config import settings
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

    # See ShopeeTHScraper.browser_mode_override — same escape hatch, empty
    # by default so shopee_br stays on the fast global BROWSER_MODE
    # (Bright Data Unlocker) until SHOPEE_BR_BROWSER_MODE_OVERRIDE is
    # explicitly set to "local", which routes through the generic
    # browser-driven ShopeeScraper.fetch_pdp() instead — no method override
    # needed here since that's already the registry's default branch for
    # any browser_mode it doesn't specifically recognize.
    @property
    def browser_mode_override(self) -> str:
        return settings.shopee_br_browser_mode_override
