"""Optional authenticated session for shopee_br scraping. When
SHOPEE_LOGIN_ENABLED=true, logs into a real Shopee account once at startup
and caches that session's storage_state (cookies + localStorage) so scrape
contexts can be created already-authenticated instead of anonymous.

Shopee's login form markup isn't guaranteed stable and the flow may show a
CAPTCHA or OTP step for automated logins — if selectors stop matching or
login silently fails, re-inspect https://shopee.com.br/buyer/login and
adjust. This is best-effort, same caveat as shopee_br.py's PDP parsing.
"""

import logging

from playwright.async_api import Browser
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth

from app.config import settings

logger = logging.getLogger(__name__)

_LOGIN_URL = "https://shopee.com.br/buyer/login"

_USERNAME_SELECTORS = [
    'input[name="loginKey"]',
    'input[placeholder*="celular" i]',
    'input[placeholder*="e-mail" i]',
    'input[placeholder*="usuário" i]',
]
_PASSWORD_SELECTORS = ['input[name="password"]', 'input[type="password"]']
# Shopee's login button has no type="submit" attribute — it's a plain
# <button> whose only reliable signal is its "ENTRAR" label (pt-BR locale).
_SUBMIT_SELECTORS = ['button:has-text("Entrar")', 'button[type="submit"]']

_COOKIE_BANNER_SELECTORS = ['button:has-text("Aceitar todos os cookies")']
# A "Selecione seu idioma" language-picker dialog covers the whole page
# (including the login form) on first visit — confirmed via DOM inspection
# that it's the actual click-blocker, not the cookie banner.
_LANGUAGE_MODAL_SELECTORS = ['button:has-text("Português (BR)")']

_cached_storage_state: dict | None = None


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


async def login_and_cache_session(browser: Browser) -> None:
    """Best-effort login; raises RuntimeError on any step it can't complete.
    Caller (browser_pool.startup) treats failure as non-fatal.
    """
    global _cached_storage_state

    context = await browser.new_context(locale="pt-BR", viewport={"width": 1366, "height": 768})
    await Stealth().apply_stealth_async(context)
    try:
        page = await context.new_page()
        await page.goto(_LOGIN_URL, timeout=settings.scrape_timeout_seconds * 1000)
        logger.debug(await page.content())
        await _dismiss_overlay(page, _LANGUAGE_MODAL_SELECTORS)
        await _dismiss_overlay(page, _COOKIE_BANNER_SELECTORS)

        if not await _fill_first_match(page, _USERNAME_SELECTORS, settings.shopee_login_username):
            raise RuntimeError("Could not find Shopee login username field — page markup may have changed")
        if not await _fill_first_match(page, _PASSWORD_SELECTORS, settings.shopee_login_password):
            raise RuntimeError("Could not find Shopee login password field — page markup may have changed")
        if not await _click_first_match(page, _SUBMIT_SELECTORS):
            raise RuntimeError("Could not find Shopee login submit button — page markup may have changed")

        await page.wait_for_load_state("networkidle", timeout=settings.scrape_timeout_seconds * 1000)

        # The login form re-renders in place on failure (wrong credentials,
        # CAPTCHA, OTP challenge) — if it's still present, we're not actually
        # logged in, and caching this state would silently scrape anonymously
        # while believing it's authenticated.
        still_on_login_form = await page.locator(_USERNAME_SELECTORS[0]).first.count() > 0
        if still_on_login_form:
            raise RuntimeError(
                "Login form still present after submit — credentials rejected, "
                "or blocked by CAPTCHA/OTP challenge"
            )

        _cached_storage_state = await context.storage_state()
        logger.info("Shopee login succeeded; session cached for reuse")
    finally:
        await context.close()


def get_cached_storage_state() -> dict | None:
    return _cached_storage_state
