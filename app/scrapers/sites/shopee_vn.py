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
    # See shopee_th.py's comment — same fix applies here as a precaution;
    # not yet independently confirmed necessary for shopee.vn specifically.
    unlocker_country = "vn"
    # Sourced from a real user report of a dead shopee.vn product link
    # showing this exact phrase; not yet confirmed against a live response
    # captured by this scraper itself — verify against a real dead product
    # page and tighten/correct if it ever produces a false positive.
    not_found_signature = "sản phẩm không tồn tại"
