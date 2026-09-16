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
    # not_found_signature left unset (unverified localized copy) — same as
    # shopee_th.py. Not needed in practice: a dead/invalid item_id or shop_id
    # is already caught generically by _parse_pdp_page_html's _PDP_FETCH_ERROR
    # check (confirmed live 2026-09-16, error code 266900002 — same code and
    # same ProductNotFoundError raw shape {bff_meta, error, error_msg, data}
    # that shopee_br gets via its own default Web Unlocker transport, since
    # both route through the same shared _parse_pdp_page_html tail).

    # Confirmed live 2026-09-16: the browser-driven local transport and
    # Bright Data's Web Unlocker API (this site's global-default transport)
    # both get risk-control rejected on the live pdp/get_pc call, same wall
    # shopee_br/th/vn hit — needs BRIGHTDATA_RESIDENTIAL_ZONE's country
    # targeting to include "sg" (see proxy_provider.py's "-country-<cc>"
    # suffix) just to get past the proxy tunnel itself, separate from that
    # wall. curl_cffi (see _shopee_common.py's _curl_cffi_fetch) gets past
    # the wall for the page load itself — confirmed live recovering a real
    # title, currency, and 9 image URLs — but not for the internal
    # pdp/get_pc call specifically, so price/rating/sold_count stay null on
    # this transport, same ceiling as shopee_th's curl_cffi path.
    @property
    def browser_mode_override(self) -> str:
        return settings.shopee_sg_browser_mode_override

    async def fetch_pdp_via_apify(self, url: str) -> PDPData:
        # Same xtracto actor shopee_vn uses — not yet independently confirmed
        # to cover shopee.sg (blocked on a 403 from the Apify account itself
        # as of 2026-09-16, reproduced against shopee_vn too, so not
        # shopee_sg-specific — retest once that account/token issue is fixed).
        return await self._apify_fetch_xtracto(url)

    async def fetch_pdp_via_curl_cffi(self, url: str) -> PDPData:
        return await self._curl_cffi_fetch(url)
