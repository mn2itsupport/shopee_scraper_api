"""CAPTCHA / anti-bot wall detection only — this project does not attempt to
defeat challenges. On detection, callers retry with a fresh browser context
(and rotated proxy) a bounded number of times, then give up and report
`captcha_blocked` so the client can retry later or the operator can investigate.

`CaptchaSolver` is a pluggable no-op hook: if you choose to integrate a
third-party solving service yourself, implement it here and call it from the
site adapter before giving up.
"""

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from urllib.parse import parse_qs, urlparse

from playwright.async_api import Page

from app.config import settings

logger = logging.getLogger(__name__)

# Known signatures Shopee (and similar anti-bot vendors) show instead of the real page.
# Extend this list as new patterns are observed; it's intentionally simple text/URL matching,
# not fingerprint evasion.
_CAPTCHA_SIGNATURES = [
    "verify you are human",
    "verifique que você é humano",
    "unusual traffic",
    "captcha",
]
_CAPTCHA_URL_FRAGMENTS = ["/verify", "/challenge"]
_SCRIPT_OR_STYLE_TAG = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


def _classify_captcha_url(url: str) -> str | None:
    """Returns a short, specific reason string for a captcha/anti-bot URL, or
    None if the URL doesn't match. Distinguishes Shopee's own named "scene"
    (e.g. scene=crawler_item, confirmed live 2026-09-20 on both shopee_th and
    shopee_br — Shopee explicitly labeling the request as detected crawler
    traffic) from a generic /verify or /challenge redirect, so server logs
    say *what kind* of wall was hit instead of just "captcha", which is what
    actually lets a block-rate trend be diagnosed later instead of
    re-discovered by hand each time.
    """
    lowered = url.lower()
    if not any(fragment in lowered for fragment in _CAPTCHA_URL_FRAGMENTS):
        return None
    scene = parse_qs(urlparse(url).query).get("scene", [None])[0]
    path = urlparse(url).path
    return f"anti-bot redirect to {path} (scene={scene})" if scene else f"anti-bot redirect to {path}"


async def is_captcha_page(page: Page) -> str | None:
    """Returns a short classification string if `page` looks like a captcha/
    anti-bot wall, or None otherwise. (Any non-None/non-empty string is
    truthy, so existing `if await is_captcha_page(page):`-style callers keep
    working unchanged.)
    """
    reason = _classify_captcha_url(page.url)
    if reason:
        return reason

    try:
        # Visible text only — page.content() returns the raw HTML, which
        # includes Shopee's own bundled JS config (e.g. a
        # "pcmall-captcha": "<module-url>" entry naming its captcha
        # component's asset bundle). That substring is present in the script
        # payload on virtually every normal page load, so matching against
        # it produces false positives; inner_text only reflects what a real
        # visitor would actually see rendered.
        text = (await page.inner_text("body")).lower()
    except Exception:
        return None

    matched = next((s for s in _CAPTCHA_SIGNATURES if s in text), None)
    return f'page text matched captcha signature "{matched}"' if matched else None


def strip_script_and_style(html: str) -> str:
    """Best-effort "visible text only" for callers that only have a raw HTML
    string (no live Page to call inner_text on) — e.g. the Web Unlocker REST
    API transport. Frontend bundles routinely embed strings that look like
    real page content (i18n dictionaries, error-page templates, module
    names) inside <script> tags on every page regardless of what's actually
    shown, so any raw-HTML substring check needs this first.
    """
    return _SCRIPT_OR_STYLE_TAG.sub(" ", html)


def is_captcha_html(html: str, url: str = "") -> str | None:
    """Same classification as is_captcha_page, for callers that only have a
    raw HTML string (no live Page) — e.g. the Web Unlocker REST API
    transport. `url` is optional (defaults to no URL-based check) since some
    callers only ever have the response body.
    """
    if url:
        reason = _classify_captcha_url(url)
        if reason:
            return reason
    text = strip_script_and_style(html).lower()
    matched = next((s for s in _CAPTCHA_SIGNATURES if s in text), None)
    return f'page text matched captcha signature "{matched}"' if matched else None


class CaptchaSolver(ABC):
    @abstractmethod
    async def solve(self, page: Page) -> bool:
        """Return True if the challenge was resolved and the page can be retried in place."""
        raise NotImplementedError


class NoOpCaptchaSolver(CaptchaSolver):
    async def solve(self, page: Page) -> bool:
        return False


class BrightDataCaptchaSolver(CaptchaSolver):
    """Uses Bright Data Scraping Browser's proprietary `Captcha.waitForSolve`
    CDP command (only available on pages served through their Scraping
    Browser product — see BROWSER_MODE=brightdata_cdp). Method name confirmed
    against Bright Data's own reference script, not just doc prose.
    """

    async def solve(self, page: Page) -> bool:
        client = await page.context.new_cdp_session(page)
        detect_timeout_ms = settings.scrape_timeout_seconds * 1000
        try:
            # Belt-and-suspenders on top of detectTimeout: if the CDP call itself
            # hangs (dropped session, Bright Data-side stall) rather than
            # returning within its own reported timeout, don't let it block the
            # request forever.
            result = await asyncio.wait_for(
                client.send("Captcha.waitForSolve", {"detectTimeout": detect_timeout_ms}),
                timeout=settings.scrape_timeout_seconds + 5,
            )
        except asyncio.TimeoutError:
            return False
        return result.get("status") == "solve_finished"


def get_captcha_solver() -> CaptchaSolver:
    if settings.browser_mode == "brightdata_cdp":
        return BrightDataCaptchaSolver()
    return NoOpCaptchaSolver()
