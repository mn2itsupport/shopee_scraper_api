from abc import ABC, abstractmethod

from playwright.async_api import BrowserContext

from app.models.schemas import PDPData


def accept_language_for(locale: str) -> str:
    """Real Chrome sends a quality-weighted fallback chain derived from the
    OS's configured language list (e.g. "th-TH,th;q=0.9,en-US;q=0.8,en;q=0.7"),
    never a bare locale tag on its own — a single-value Accept-Language is
    itself a passive fingerprint. Shared by every transport that needs to
    force a realistic header for a given site's locale: browser_pool.py
    (Playwright's `locale` context option only ever produces the bare tag)
    and _shopee_common.py's curl_cffi transport (its `impersonate=` profile
    defaults to a fixed "en-US,en;q=0.9" regardless of target country).
    """
    lang = locale.split("-")[0]
    if lang == "en":
        return f"{locale},en;q=0.9"
    return f"{locale},{lang};q=0.9,en-US;q=0.8,en;q=0.7"


class ScraperError(Exception):
    """Raised for a failure that isn't a CAPTCHA/anti-bot wall (see CaptchaBlockedError)."""


class ProductNotFoundError(ScraperError):
    """Raised when the site confirms the product doesn't exist (dead/removed listing),
    as opposed to an actual scrape failure — callers treat this as a successful scrape
    with no data rather than an error.

    `raw`, when the detection path has one, is the site's own not-found envelope
    (e.g. Shopee's {bff_meta, error, error_msg, data} shape — real key order, per
    a live capture — with the real numeric error code) — callers surface this as
    the response's `data` verbatim, same as a found product's raw payload, rather
    than returning `data: null`. Detection paths with no such envelope to confirm
    against fall back to a placeholder with every field null.
    """

    def __init__(self, message: str, *, raw: dict | None = None) -> None:
        super().__init__(message)
        self.raw = raw if raw is not None else {"bff_meta": None, "error": None, "error_msg": None, "data": None}


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
    # Two-letter country code passed as the Web Unlocker API's "country" param
    # (brightdata_unlocker_api mode only) so Bright Data exits through that
    # country's residential IPs instead of auto-picking — for some targets
    # (confirmed: shopee.co.th) an auto-picked exit IP gets flagged by the
    # site's anti-bot layer and the Unlocker never renders a real page even
    # after 90s+, while forcing the matching country resolves in ~15s. Leave
    # "" to let Bright Data auto-select (fine for targets that don't need it,
    # e.g. shopee.com.br). Also used as the "-country-<cc>" suffix for
    # PROXY_MODE=brightdata_residential (see proxy_provider.py) when this
    # site's browser_mode_override routes it through the browser transport.
    unlocker_country: str = ""

    # Per-site override of settings.browser_mode (app/config.py) — None
    # means "use the global setting" like every other site. Set this when a
    # site needs a different transport than the rest (e.g. shopee_th forcing
    # "local" so it goes through acquire_context()/fetch_pdp() and picks up
    # its cached login session + country-targeted proxy, even while the
    # global default stays brightdata_unlocker_api for other sites).
    browser_mode_override: str | None = None

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

    async def fetch_pdp_via_dataset_api(self, url: str) -> PDPData:
        """Fetch `url` via one of Bright Data's maintained per-site Dataset
        API scrapers (no browser/Playwright involved) and return normalized
        PDP data. Only called when BROWSER_MODE=brightdata_dataset_api;
        optional to implement — sites that don't override this simply can't
        run in that mode.
        """
        raise NotImplementedError(f"{self.site_key} does not support the Bright Data Dataset API transport")

    async def fetch_pdp_via_apify(self, url: str) -> PDPData:
        """Fetch `url` via a third-party Apify actor (no browser/Playwright
        involved — a single synchronous HTTP call, the actor's own
        infrastructure handles anti-bot server-side) and return normalized
        PDP data. Only called when BROWSER_MODE (or a site's own
        browser_mode_override, e.g. ShopeeTHScraper's) is "apify"; optional
        to implement — sites that don't override this simply can't run in
        that mode.
        """
        raise NotImplementedError(f"{self.site_key} does not support the Apify transport")

    async def fetch_pdp_via_curl_cffi(self, url: str) -> PDPData:
        """Fetch `url` with curl_cffi (a plain HTTP client impersonating a
        real browser's TLS/JA3 fingerprint — no Playwright/CDP involved) and
        return normalized PDP data. Only called when BROWSER_MODE (or a
        site's own browser_mode_override, e.g. ShopeeTHScraper's) is
        "curl_cffi"; optional to implement — sites that don't override this
        simply can't run in that mode.
        """
        raise NotImplementedError(f"{self.site_key} does not support the curl_cffi transport")
