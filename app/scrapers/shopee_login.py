"""Optional authenticated session for Shopee scraping. When a given site's
SHOPEE_*_LOGIN_ENABLED is true, logs into a real Shopee account once at
startup and caches that session's storage_state (cookies + localStorage) so
scrape contexts for that site can be created already-authenticated instead
of anonymous.

The username/password/submit selectors below are shared across every Shopee
country storefront (same underlying platform — confirmed by inspecting both
shopee.com.br and shopee.co.th's live login pages: both render
input[name="loginKey"], input[name="password"], and a submit button). Only
the login URL, locale, and any cookie/language overlay are country-specific,
so a new site's config is a few lines in enabled_configs() below, not a new
selector set.

Shopee's login form markup isn't guaranteed stable and the flow may show a
CAPTCHA or OTP step for automated logins — if selectors stop matching or
login silently fails, re-inspect that site's own /buyer/login page and
adjust. This is best-effort, same caveat as _shopee_common.py's PDP parsing.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from patchright.async_api import TimeoutError as PatchrightTimeoutError
from playwright.async_api import Browser, BrowserContext
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth

from app.config import settings
from app.scrapers.proxy_provider import get_proxy_provider

logger = logging.getLogger(__name__)

_USERNAME_SELECTORS = [
    'input[name="loginKey"]',
    'input[placeholder*="celular" i]',
    'input[placeholder*="e-mail" i]',
    'input[placeholder*="usuário" i]',
]
_PASSWORD_SELECTORS = ['input[name="password"]', 'input[type="password"]']
# 'button[type="submit"]' alone is NOT safe as a catch-all fallback: Shopee's
# login page also renders a visually-hidden "skip to main content" a11y link
# styled as button[type="submit"] earlier in the DOM (confirmed via DOM
# inspection on shopee.co.th) — since _click_first_match takes .first per
# selector, it latches onto that hidden element and times out waiting for it
# to become visible, never reaching the real login button. Every site's
# config below must lead with a locale-specific text match that uniquely
# identifies the actual submit button; this generic selector is a last
# resort only (kept for forward-compat with a country not yet configured).
_SUBMIT_SELECTORS = ['button[type="submit"]']

_LANGUAGE_MODAL_SELECTORS: list[str] = []
_COOKIE_BANNER_SELECTORS: list[str] = []

# A "Selecione seu idioma" language-picker dialog covers the whole
# shopee.com.br login page (including the form) on first visit — confirmed
# via DOM inspection that it's the actual click-blocker, not the cookie
# banner. Not observed on shopee.co.th's login page.
_BR_LANGUAGE_MODAL_SELECTORS = ['button:has-text("Português (BR)")']
_BR_COOKIE_BANNER_SELECTORS = ['button:has-text("Aceitar todos os cookies")']


@dataclass
class SiteLoginConfig:
    site_key: str
    login_url: str
    locale: str
    username: str
    password: str
    # Must lead with a selector that uniquely and directly matches this
    # site's real login submit button — see the _SUBMIT_SELECTORS comment
    # above for why the generic type="submit" fallback alone isn't safe.
    submit_selectors: list[str] = field(default_factory=lambda: list(_SUBMIT_SELECTORS))
    language_modal_selectors: list[str] = field(default_factory=list)
    cookie_banner_selectors: list[str] = field(default_factory=list)
    # Two-letter country code for PROXY_MODE=brightdata_residential country
    # targeting (see BaseScraper.unlocker_country) — leave "" to log in from
    # whatever IP the process itself runs on. Only matters when
    # via_brightdata_cdp is False (Bright Data's Scraping Browser routes its
    # own exit IP regardless of this).
    country: str = ""
    # When True, login runs through a separate, temporary Bright Data
    # Scraping Browser connection (settings.brightdata_ws_endpoint) instead
    # of the shared local Chromium instance — no local proxy/stealth
    # override applied, same reasoning as browser_pool.py's ManagedContext
    # for BROWSER_MODE=brightdata_cdp. shopee_th needs this: a plain local
    # Chromium login attempt (with or without a country-targeted proxy —
    # confirmed proxy alone made no difference) gets an invisible,
    # never-clearing "#modal" overlay intercepting the submit click, which
    # looks like browser-fingerprint detection rather than an IP-reputation
    # check, so it's worth letting Bright Data's own anti-fingerprinting
    # handle the connection instead.
    login_via_brightdata_cdp: bool = False


def enabled_configs() -> list[SiteLoginConfig]:
    """One entry per site with login enabled in settings. Called from
    browser_pool.startup(); a site with its *_LOGIN_ENABLED flag off is
    simply absent, and its scrapes stay anonymous."""
    configs = []
    if settings.shopee_login_enabled:
        configs.append(
            SiteLoginConfig(
                site_key="shopee_br",
                login_url="https://shopee.com.br/buyer/login",
                locale="pt-BR",
                username=settings.shopee_login_username,
                password=settings.shopee_login_password,
                submit_selectors=['button:has-text("Entrar")'] + _SUBMIT_SELECTORS,
                language_modal_selectors=_BR_LANGUAGE_MODAL_SELECTORS,
                cookie_banner_selectors=_BR_COOKIE_BANNER_SELECTORS,
                # Route login through the same BR residential proxy the
                # scrape contexts use (PROXY_MODE=brightdata_residential) —
                # logging in from one IP and then scraping with that
                # session's cookies from a different exit IP/geo is itself a
                # fraud-detection signal, so keep them consistent.
                country="br",
            )
        )
    if settings.shopee_th_login_enabled:
        configs.append(
            SiteLoginConfig(
                site_key="shopee_th",
                login_url="https://shopee.co.th/buyer/login",
                locale="th-TH",
                username=settings.shopee_th_login_username,
                password=settings.shopee_th_login_password,
                # Thai for "Log in" — confirmed as the actual submit button's
                # unique visible text via live DOM inspection.
                submit_selectors=['button:has-text("เข้าสู่ระบบ")'] + _SUBMIT_SELECTORS,
                country="th",
                login_via_brightdata_cdp=True,
            )
        )
    return configs


_cached_storage_state: dict[str, dict] = {}


async def _dismiss_overlay(page, selectors: list[str], timeout_ms: int = 4000) -> None:
    # Not fatal if absent (already dismissed / never shown) — swallow timeouts.
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            continue
        await locator.click()
        return


async def _fill_first_match(page, selectors: list[str], value: str, timeout_ms: int = 8000) -> bool:
    # Shopee's login form renders client-side after an anti-fraud check —
    # locator.count() checks instantly with no wait, so it can miss a form
    # that's still hydrating. wait_for(state="visible") actually waits.
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            continue
        await locator.fill(value)
        return True
    return False


async def _save_debug_artifacts(page, site_key: str, step: str) -> None:
    """Best-effort screenshot + HTML dump when a login step fails, so a
    silent markup/timing change can actually be diagnosed instead of just
    retried blindly. Never lets a capture failure mask the real error."""
    try:
        debug_dir = Path(settings.log_dir) / "shopee_login_debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(debug_dir / f"{site_key}_{step}.png"))
        (debug_dir / f"{site_key}_{step}.html").write_text(await page.content(), encoding="utf-8")
        logger.warning(
            "Saved Shopee login debug artifacts for %s (%s) to %s — page.url=%s",
            site_key,
            step,
            debug_dir,
            page.url,
        )
    except Exception:
        logger.warning("Failed to save Shopee login debug artifacts for %s (%s)", site_key, step, exc_info=True)


async def _click_first_match(page, selectors: list[str], timeout_ms: int = 8000) -> bool:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            continue
        await locator.click()
        return True
    return False


async def login_and_cache_session(browser: Browser, config: SiteLoginConfig) -> None:
    """Best-effort login for one site; raises RuntimeError on any step it
    can't complete. Caller (browser_pool.startup) treats failure as
    non-fatal per site.
    """
    if config.login_via_brightdata_cdp:
        # Bright Data's Scraping Browser handles proxy/exit-IP rotation and
        # fingerprinting on its own side — local proxy/stealth overrides
        # would just fight its own patches (same reasoning as
        # browser_pool.py's ManagedContext for BROWSER_MODE=brightdata_cdp).
        context = await browser.new_context(locale=config.locale, viewport={"width": 1366, "height": 768})
    else:
        proxy = get_proxy_provider().next_proxy(country=config.country) if config.country else None
        context = await browser.new_context(
            locale=config.locale,
            viewport={"width": 1366, "height": 768},
            proxy=proxy,
            # Matches ManagedContext's reasoning in browser_pool.py: Bright
            # Data's Web Unlocker (and similar unblocking proxies) MITM the
            # TLS connection and present their own certificate.
            ignore_https_errors=settings.proxy_mode == "brightdata_unlocker",
        )
        await Stealth().apply_stealth_async(context)
    try:
        page = await context.new_page()
        await page.goto(config.login_url, timeout=settings.scrape_timeout_seconds * 1000)
        await _dismiss_overlay(page, config.language_modal_selectors)
        await _dismiss_overlay(page, config.cookie_banner_selectors)

        if not await _fill_first_match(page, _USERNAME_SELECTORS, config.username):
            await _save_debug_artifacts(page, config.site_key, "no_username_field")
            raise RuntimeError(
                f"[{config.site_key}] Could not find Shopee login username field — page markup may have changed"
            )
        if not await _fill_first_match(page, _PASSWORD_SELECTORS, config.password):
            await _save_debug_artifacts(page, config.site_key, "no_password_field")
            raise RuntimeError(
                f"[{config.site_key}] Could not find Shopee login password field — page markup may have changed"
            )
        # A debug capture showed the login form (input[name="loginKey"])
        # entirely gone from the DOM right after filling password, with a
        # blank-white screenshot — looked like a mid-transition SPA
        # re-render rather than a real redirect; give it a moment to settle
        # before deciding the submit button truly isn't there.
        await page.wait_for_timeout(2000)
        # Longer than the other steps' default 8s: a Bright Data Scraping
        # Browser connection (login_via_brightdata_cdp) adds real latency on
        # top of Shopee's own render time for this step specifically.
        if not await _click_first_match(page, config.submit_selectors, timeout_ms=20000):
            await _save_debug_artifacts(page, config.site_key, "no_submit_button")
            raise RuntimeError(
                f"[{config.site_key}] Could not find Shopee login submit button — page markup may have changed"
            )

        await page.wait_for_load_state("networkidle", timeout=settings.scrape_timeout_seconds * 1000)

        # The login form re-renders in place on failure (wrong credentials,
        # CAPTCHA, OTP challenge) — if it's still present, we're not actually
        # logged in, and caching this state would silently scrape anonymously
        # while believing it's authenticated.
        still_on_login_form = await page.locator(_USERNAME_SELECTORS[0]).first.count() > 0
        if still_on_login_form:
            await _save_debug_artifacts(page, config.site_key, "still_on_login_form")
            raise RuntimeError(
                f"[{config.site_key}] Login form still present after submit — credentials rejected, "
                "or blocked by CAPTCHA/OTP challenge"
            )

        _cached_storage_state[config.site_key] = await context.storage_state()
        logger.info("Shopee login succeeded for %s; session cached for reuse", config.site_key)
    finally:
        await context.close()


def get_cached_storage_state(site_key: str) -> dict | None:
    return _cached_storage_state.get(site_key)


async def check_persistent_profile_session_valid(
    context: BrowserContext, login_url: str, timeout_ms: int = 20000
) -> bool | None:
    """Best-effort check of whether a persistent-profile site's session
    (browser_pool.py's _shopee_th_context / _shopee_br_context) is still
    actually authenticated, rather than just trusting that cookies exist on
    disk — a session can go stale (expired, revoked) while its cookies are
    still present, and a bare cookie-count check can't tell the difference.
    Called once at startup per site, then on that site's own watchdog tick.

    Returns True if logged in, False if the login form is still reachable
    (session is dead — rerun the site's manual-login script to refresh it),
    or None if the check itself got caught by Shopee's own
    traffic-verification wall (inconclusive — the session might still be
    fine, this check just couldn't confirm it either way).
    """
    page = await context.new_page()
    try:
        await page.goto(login_url, timeout=timeout_ms)
        if "/verify/traffic/error" in page.url:
            return None
        try:
            # Same signal scripts/shopee_th_manual_login.py's own poll loop
            # uses: an already-authenticated session gets redirected away
            # from the login form before this resolves, so a timeout here
            # (not the form appearing) is what "logged in" looks like.
            await page.locator(_USERNAME_SELECTORS[0]).first.wait_for(state="visible", timeout=timeout_ms)
            return False
        except (PlaywrightTimeoutError, PatchrightTimeoutError):
            # context is always Patchright's for the one real caller
            # (browser_pool.py's _shopee_th_context) — its TimeoutError is a
            # distinct class from Playwright's own, not a subclass, so both
            # need catching here regardless of which one actually launched
            # this context.
            return True
    finally:
        await page.close()
