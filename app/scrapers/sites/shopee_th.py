"""Shopee Thailand PDP adapter. Shared logic lives in _shopee_common.py — this
file only pins the country-specific constants."""

from app.scrapers.sites._shopee_common import ShopeeScraper


class ShopeeTHScraper(ShopeeScraper):
    site_key = "shopee_th"
    base_domain = "shopee.co.th"
    default_currency = "THB"
    locale = "th-TH"
    timezone_id = "Asia/Bangkok"
    geolocation = {"latitude": 13.7563, "longitude": 100.5018}
    # Required in practice, not just a nice-to-have: without this, Bright
    # Data's Web Unlocker auto-picks an exit IP that Shopee TH's anti-bot
    # layer flags, and the request never resolves (still failing after
    # 90s+ waiting for the product selector to render) — confirmed by
    # testing the Unlocker API directly. Forcing "th" resolves in ~15s.
    unlocker_country = "th"
    # not_found_signature left unset (unverified localized copy) — a
    # nonexistent product still falls through to the HTML/DOM fallback
    # instead of raising ScraperError; fill in once confirmed against a real
    # dead product page, same as shopee_br.py's.
