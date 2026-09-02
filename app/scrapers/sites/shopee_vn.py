"""Shopee Vietnam PDP adapter. Shared logic lives in _shopee_common.py — this
file only pins the country-specific constants."""

from app.scrapers.sites._shopee_common import ShopeeScraper


class ShopeeVNScraper(ShopeeScraper):
    site_key = "shopee_vn"
    base_domain = "shopee.vn"
    default_currency = "VND"
    locale = "vi-VN"
    timezone_id = "Asia/Ho_Chi_Minh"
    geolocation = {"latitude": 10.8231, "longitude": 106.6297}
    # not_found_signature left unset (unverified localized copy) — a
    # nonexistent product still falls through to the HTML/DOM fallback
    # instead of raising ScraperError; fill in once confirmed against a real
    # dead product page, same as shopee_br.py's.
