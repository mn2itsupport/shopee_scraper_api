"""One-time manual login for shopee_br's persistent browser profile.

Run this locally from the shopee_scraper_api/ directory so .env is found
(server can be stopped or running — it opens its own Chromium against the
same profile directory the app uses for shopee_br):

    python scripts/shopee_br_manual_login.py

It opens a real, VISIBLE browser window at Shopee BR's login page. Log in
by hand — your own credentials, your own click/type actions. This script
never touches any password itself; it only opens the page and waits.

Why this exists: every *automated* shopee_br attempt tried so far —
anonymous local Chromium+stealth, Bright Data's Scraping Browser over CDP,
and even an automated login-and-cache-session run through the same
residential proxy — got CaptchaBlockedError on the very next automated
action (confirmed via repeated live testing 2026-09-10 — see
app/scrapers/shopee_login.py and browser_pool.py's _shopee_br_context).
That matches shopee_th's own documented history, where only a real,
human-driven login got past it. Once you're done here, the resulting
cookies/localStorage are saved to browser_profiles/shopee_br/ on disk and
reused by every later automated shopee_br scrape — no repeat login needed
until the session eventually expires.

IMPORTANT: this launches with the *same* proxy/timezone/geolocation config
browser_pool.py's _shopee_br_context uses for real scraping afterward
(everything except headless, which has to stay visible here), and uses
Patchright rather than plain Playwright + Stealth() for the same reason
shopee_th's own manual-login script does — Patchright patches the CDP
Runtime.enable/Console.enable leaks and automation flags that
playwright-stealth doesn't cover; a fresh human login under plain
Playwright risks getting invalidated on the very next automated action the
same way it did for shopee_th. Keep this in sync with browser_pool.py's
launch_persistent_context call if that ever changes.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from patchright.async_api import async_playwright  # noqa: E402
from patchright.async_api import TimeoutError as PlaywrightTimeoutError  # noqa: E402

from app.config import settings  # noqa: E402
from app.scrapers.proxy_provider import get_proxy_provider  # noqa: E402
from app.scrapers.sites.shopee_br import ShopeeBRScraper  # noqa: E402

PROFILE_DIR = Path(__file__).resolve().parent.parent / "browser_profiles" / "shopee_br"
LOGIN_URL = "https://shopee.com.br/buyer/login"
# No Enter-key prompt here on purpose: a bare input() depends on a real
# interactive stdin, which isn't reliably available depending on how this
# script gets launched — polling the page itself for a successful-login
# signal works regardless of how it's run.
MAX_WAIT_SECONDS = 600
POLL_INTERVAL_SECONDS = 2


_CONNECT_MAX_ATTEMPTS = 6


async def _open_login_page(p):
    """Launch the persistent context and navigate to LOGIN_URL, retrying on
    the Bright Data residential proxy's intermittent net::ERR_INVALID_AUTH_CREDENTIALS
    (same intermittent-bad-exit-node issue shopee_th_manual_login.py's own
    retry loop was added for) — each retry gets a fresh proxy assignment via
    a brand-new context.
    """
    for attempt in range(1, _CONNECT_MAX_ATTEMPTS + 1):
        # Mirrors browser_pool.py's _shopee_br_context launch exactly, so the
        # session is established under the same fingerprint it'll actually be
        # used under afterward — see the module docstring for why.
        proxy = (
            get_proxy_provider().next_proxy(country=ShopeeBRScraper.unlocker_country)
            if settings.proxy_mode == "brightdata_residential"
            else None
        )
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            locale=ShopeeBRScraper.locale,
            timezone_id=ShopeeBRScraper.timezone_id,
            geolocation=ShopeeBRScraper.geolocation,
            permissions=["geolocation"] if ShopeeBRScraper.geolocation else [],
            viewport={"width": 1366, "height": 768},
            proxy=proxy,
            ignore_https_errors=settings.proxy_mode == "brightdata_unlocker",
        )
        page = await context.new_page()
        try:
            await page.goto(LOGIN_URL, timeout=20000)
            return context, page
        except Exception as exc:
            print(f"Connect attempt {attempt}/{_CONNECT_MAX_ATTEMPTS} failed: {type(exc).__name__}: {str(exc)[:150]}")
            await context.close()
            if attempt == _CONNECT_MAX_ATTEMPTS:
                raise
            await asyncio.sleep(2)


async def main() -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        context, page = await _open_login_page(p)

        print(f"Opened {LOGIN_URL} in a real browser window.")

        # A "Selecione seu idioma" language-picker dialog covers the whole
        # login page on first visit (see shopee_login.py's
        # _BR_LANGUAGE_MODAL_SELECTORS) — dismiss it by hand if it appears,
        # same as the cookie banner; both are just clicks, not part of the
        # login form itself, so no special handling needed here beyond
        # letting you see and click past them in the visible window.

        try:
            await page.locator('input[name="loginKey"]').first.wait_for(state="visible", timeout=30000)
            print("Login form loaded. Log in by hand now.")
        except PlaywrightTimeoutError:
            print(
                "Login form didn't appear within 30s — it may be hidden behind the language/cookie "
                "dialog. Dismiss those by hand, then log in. Waiting anyway..."
            )

        print(f"Waiting up to {MAX_WAIT_SECONDS // 60} minutes for you to finish logging in...")

        deadline = asyncio.get_event_loop().time() + MAX_WAIT_SECONDS
        logged_in = False
        while asyncio.get_event_loop().time() < deadline:
            try:
                still_on_login_form = await page.locator('input[name="loginKey"]').first.count() > 0
            except Exception:
                # Page mid-navigation or the window itself got closed — keep
                # waiting rather than treating a transient error as failure.
                still_on_login_form = True
            if not still_on_login_form:
                logged_in = True
                break
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

        if logged_in:
            print("Login form is gone — looks like you're logged in. Saving session...")
        else:
            print(f"Still on the login form after {MAX_WAIT_SECONDS}s — saving whatever state exists anyway.")

        await context.close()
        print(f"Saved to {PROFILE_DIR} — the app will reuse this profile for shopee_br from now on.")


if __name__ == "__main__":
    asyncio.run(main())
