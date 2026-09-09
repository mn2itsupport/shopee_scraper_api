"""Shopee Thailand PDP adapter. Shared logic lives in _shopee_common.py — this
file only pins the country-specific constants."""

from app.config import settings
from app.models.schemas import PDPData
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

    # Every *anonymous* transport tried against Shopee TH (bare Playwright,
    # Bright Data Web Unlocker REST, Bright Data's own Scraping Browser) got
    # risk-control rejected on the live pdp/get_pc call, which is the only
    # source of real price/rating/sold data — the Unlocker transport's HTML
    # fallback (_extract_pdp_bff_data) gets those fields nulled out by Shopee
    # regardless of item validity. And every *authenticated* browser-driven
    # attempt (local headless/headed, Patchright, Bright Data's Scraping
    # Browser with a real human login) still ran into some form of the same
    # anti-bot wall on the very next automated action — confirmed via
    # extensive live testing, not just anonymous access. SHOPEE_TH_BROWSER_MODE_OVERRIDE
    # (settings.shopee_th_browser_mode_override) picks the transport for this
    # one site independent of every other site's BROWSER_MODE: "local" is
    # that persistent-profile + Patchright path; "apify" routes through a
    # third-party Apify actor instead (see _shopee_common.py's _apify_fetch)
    # which avoids the anti-bot fight entirely by running server-side.
    @property
    def browser_mode_override(self) -> str:
        return settings.shopee_th_browser_mode_override

    async def fetch_pdp_via_dataset_api(self, url: str) -> PDPData:
        return await self._dataset_api_fetch(url)

    async def fetch_pdp_via_apify(self, url: str) -> PDPData:
        return await self._apify_fetch(url)
