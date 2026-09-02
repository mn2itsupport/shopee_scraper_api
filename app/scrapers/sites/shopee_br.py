"""Shopee Brazil PDP adapter. Shared logic lives in _shopee_common.py — this
file only pins the country-specific constants."""

from app.scrapers.sites._shopee_common import ShopeeScraper


class ShopeeBRScraper(ShopeeScraper):
    site_key = "shopee_br"
    base_domain = "shopee.com.br"
    default_currency = "BRL"
    locale = "pt-BR"
    timezone_id = "America/Sao_Paulo"
    geolocation = {"latitude": -23.5505, "longitude": -46.6333}
    not_found_signature = "o produto não existe"
