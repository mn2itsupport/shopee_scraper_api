"""Shopee Brazil PDP adapter. Shared logic lives in _shopee_common.py — this
file only pins the country-specific constants."""

import logging

from app.config import settings
from app.models.schemas import PDPData
from app.scrapers.base import ScraperError
from app.scrapers.sites._shopee_common import ShopeeScraper

logger = logging.getLogger(__name__)


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
        """Entry point when SHOPEE_BR_BROWSER_MODE_OVERRIDE=apify (the
        current default). Previously a bare call to _apify_fetch — a single
        actor hiccup (cache miss, Apify account cap, transient 5xx) failed
        the whole request even though this class already implements two
        other transports that never get used unless someone flips the env
        var by hand. Chains through all three, same idiom as
        ShopeeTHScraper.fetch_pdp_via_apify's apify -> curl_cffi fallback:
        each is a genuinely independent vendor/infrastructure, not a retry
        of the same failure mode, so falling through is worth the extra
        latency on a real failure.

        Catches ScraperError, not just its ProductNotFoundError subclass,
        for the same reason documented on ShopeeTHScraper's version: gio21's
        "no records" signal can't be proven to distinguish a genuinely dead
        item from one the actor hasn't cached/covered (unconfirmed either
        way for BR specifically, unlike the xtracto actor where this was
        confirmed live) — falling through costs a bit of latency on a real
        not-found (the next transport just confirms the same result) but
        avoids trusting an unverified not-found heuristic as final.
        """
        last_error: ScraperError
        try:
            return await self._apify_fetch(url)
        except ScraperError as exc:
            last_error = exc
            logger.warning("shopee_br apify transport failed for %s, falling back: %s", url, exc)

        # Bright Data's maintained Shopee dataset scraper — independent
        # infrastructure from Apify. Only attempt it if a dataset id is
        # actually configured; an empty one just fails Bright Data's own
        # request validation immediately, wasting a round trip.
        if settings.brightdata_shopee_dataset_id:
            try:
                return await self._dataset_api_fetch(url)
            except ScraperError as exc:
                last_error = exc
                logger.warning("shopee_br dataset API transport failed for %s, falling back: %s", url, exc)

        # Last resort: Bright Data's Web Unlocker REST API (server-rendered
        # HTML parsed for ld+json/BFF data). Weaker than the two above (no
        # sold_count; price/rating come from ld+json only) but still real
        # data. Only attempt it if a token is configured, same reasoning as
        # dataset_id above. Can itself raise CaptchaBlockedError (not a
        # ScraperError subclass) if Bright Data's own render hits an
        # anti-bot wall — deliberately left uncaught here so it propagates
        # to scrape_with_retries' normal backoff-and-retry path instead of
        # being swallowed as a plain failure.
        if settings.brightdata_api_token:
            try:
                return await self.fetch_pdp_via_unlocker_api(url)
            except ScraperError as exc:
                last_error = exc
                logger.warning("shopee_br unlocker API transport also failed for %s: %s", url, exc)

        raise last_error
