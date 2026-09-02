from abc import ABC, abstractmethod

from playwright.async_api import BrowserContext

from app.models.schemas import PDPData


class ScraperError(Exception):
    """Raised for a failure that isn't a CAPTCHA/anti-bot wall (see CaptchaBlockedError)."""


class CaptchaBlockedError(Exception):
    """Raised when an adapter detects a CAPTCHA/anti-bot interstitial instead of real data."""


class BaseScraper(ABC):
    """Contract every website adapter implements.

    A new site = a new subclass + a registry entry (see registry.py). Nothing
    else in the app (auth, rate limiting, storage, dashboard) needs to change.
    """

    site_key: str
    base_domain: str
    # Used by browser_pool when opening a context for this site's fetch_pdp
    # (local/brightdata_cdp modes only) so the declared locale, timezone, and
    # geolocation agree with the target country instead of defaulting to
    # whichever site was tuned first — a mismatch there is an easy bot signal
    # regardless of what country the proxy's exit IP is in.
    locale: str = "en-US"
    timezone_id: str = "UTC"
    geolocation: dict | None = None

    @abstractmethod
    async def fetch_pdp(self, context: BrowserContext, url: str) -> PDPData:
        """Navigate to `url` inside the given browser context and return normalized PDP data.

        Implementations should raise CaptchaBlockedError when they detect an
        anti-bot wall instead of a product page, and ScraperError for any
        other failure (timeout, unexpected page shape, etc.).
        """
        raise NotImplementedError

    async def fetch_pdp_via_unlocker_api(self, url: str) -> PDPData:
        """Fetch `url` via Bright Data's Web Unlocker REST API (no browser
        context involved) and return normalized PDP data. Only called when
        BROWSER_MODE=brightdata_unlocker_api; optional to implement — sites
        that don't override this simply can't run in that mode.
        """
        raise NotImplementedError(f"{self.site_key} does not support the Web Unlocker REST API transport")
