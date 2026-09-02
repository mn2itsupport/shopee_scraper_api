import asyncio
import random

from app.config import settings
from app.models.schemas import PDPData
from app.scrapers.base import BaseScraper, CaptchaBlockedError, ScraperError
from app.scrapers.browser_pool import acquire_context
from app.scrapers.sites.shopee_br import ShopeeBRScraper
from app.scrapers.sites.shopee_th import ShopeeTHScraper
from app.scrapers.sites.shopee_vn import ShopeeVNScraper

# Adding a new site: implement BaseScraper in scrapers/sites/<site>.py, then
# add one line here. Nothing else in the app needs to change.
SCRAPER_REGISTRY: dict[str, BaseScraper] = {
    "shopee_br": ShopeeBRScraper(),
    "shopee_th": ShopeeTHScraper(),
    "shopee_vn": ShopeeVNScraper(),
}


def get_scraper(site_key: str) -> BaseScraper:
    scraper = SCRAPER_REGISTRY.get(site_key)
    if scraper is None:
        raise KeyError(f"Unknown site_key: {site_key}")
    return scraper


async def scrape_with_retries(site_key: str, url: str) -> PDPData:
    """Runs the adapter inside a fresh browser context, retrying on CAPTCHA
    with a brand-new context (and rotated proxy) up to CAPTCHA_MAX_RETRIES
    times. Re-raises CaptchaBlockedError if still blocked after retries.
    """
    scraper = get_scraper(site_key)
    last_error: Exception | None = None

    for attempt in range(settings.captcha_max_retries + 1):
        if settings.pre_scrape_jitter_ms_max > 0:
            jitter_ms = random.uniform(settings.pre_scrape_jitter_ms_min, settings.pre_scrape_jitter_ms_max)
            await asyncio.sleep(jitter_ms / 1000)

        try:
            if settings.browser_mode == "brightdata_unlocker_api":
                # No browser context needed — this transport is a single
                # server-side-rendered HTTP call.
                return await scraper.fetch_pdp_via_unlocker_api(url)
            async with acquire_context(scraper.locale, scraper.timezone_id, scraper.geolocation) as context:
                return await scraper.fetch_pdp(context, url)
        except CaptchaBlockedError as exc:
            last_error = exc
            # Backoff before the next fresh context; jitter on top so concurrent
            # retries across different requests don't all re-attempt in lockstep.
            # Wider than a plain rate-limit backoff needs: Shopee's wall is a
            # risk-engine traffic check, not a request-rate limit, so retrying
            # within a couple seconds looks like the same bad pattern that
            # tripped it in the first place.
            await asyncio.sleep(8 * (attempt + 1) + random.uniform(0, 4))
        except ScraperError:
            raise

    assert last_error is not None
    raise last_error
