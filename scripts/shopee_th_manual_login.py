"""One-time manual login for shopee_th's persistent browser profile.

Run this locally from the shopee_scraper_api/ directory so .env is found
(server can be stopped or running — it opens its own Chromium against the
same profile directory the app uses for shopee_th):

    python scripts/shopee_th_manual_login.py

It opens a real, VISIBLE browser window at Shopee TH's login page. Log in
by hand — your own credentials, your own click/type actions. This script
never touches any password itself; it only opens the page and waits.

Why this exists: every *automated* shopee_th login attempt (plain local
Chromium, Bright Data's Scraping Browser over CDP) got blocked by Shopee's
own traffic-verification wall before completing (confirmed via repeated live
testing — see app/scrapers/shopee_login.py and browser_pool.py's
_shopee_th_context). A real, human-driven login isn't subject to that,
and once it's done, the resulting cookies/localStorage are saved to
browser_profiles/shopee_th/ on disk and reused by every later automated
shopee_th scrape — no repeat login needed until the session eventually
expires.

IMPORTANT: this launches with the *same* proxy/timezone/geolocation config
browser_pool.py's _shopee_th_context uses for real scraping afterward
(everything except headless, which has to stay visible here) — an earlier
version of this script used plain Playwright + playwright-stealth and,
even with proxy/timezone/geolocation matched, got its session invalidated
by Shopee's anti-bot layer on the very next automated action after a real
human login (confirmed via live testing: any destination page, same or
fresh process, matched fingerprint — none of that mattered). That points
at CDP-level detection specifically (the Runtime.enable/Console.enable
leak playwright-stealth doesn't patch), so this now uses Patchright — a
patched Playwright fork built to avoid exactly that — instead of plain
Playwright + Stealth(). Keep this in sync with browser_pool.py's
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
from app.scrapers.sites.shopee_th import ShopeeTHScraper  # noqa: E402

PROFILE_DIR = Path(__file__).resolve().parent.parent / "browser_profiles" / "shopee_th"
LOGIN_URL = "https://shopee.co.th/buyer/login"
# No Enter-key prompt here on purpose: a bare input() depends on a real
# interactive stdin, which isn't reliably available depending on how this
# script gets launched (confirmed to hit immediate EOF and close the browser
# right away in at least one launch path) — polling the page itself for a
# successful-login signal works regardless of how it's run.
MAX_WAIT_SECONDS = 600
POLL_INTERVAL_SECONDS = 2


_CONNECT_MAX_ATTEMPTS = 6


async def _open_login_page(p):
    """Launch the persistent context and navigate to LOGIN_URL, retrying on
    the Bright Data residential proxy's intermittent net::ERR_INVALID_AUTH_CREDENTIALS
    (confirmed via live testing: same credentials, same everything — just an
    upstream residential exit node being briefly bad, unrelated to the
    profile directory or anything about this script) — each retry gets a
    fresh proxy assignment via a brand-new context.
    """
    for attempt in range(1, _CONNECT_MAX_ATTEMPTS + 1):
        # Mirrors browser_pool.py's _shopee_th_context launch exactly, so the
        # session is established under the same fingerprint it'll actually be
        # used under afterward — see the module docstring for why.
        proxy = (
            get_proxy_provider().next_proxy(country=ShopeeTHScraper.unlocker_country)
            if settings.proxy_mode == "brightdata_residential"
            else None
        )
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            locale=ShopeeTHScraper.locale,
            timezone_id=ShopeeTHScraper.timezone_id,
            geolocation=ShopeeTHScraper.geolocation,
            permissions=["geolocation"] if ShopeeTHScraper.geolocation else [],
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

        # input[name="loginKey"] is ALSO absent for the first couple seconds
        # after navigation, before the page's own JS bundle finishes mounting
        # the form — checking only "is it gone" as the login-succeeded signal
        # was a false positive on that initial empty state, closing the
        # browser within seconds of opening it (confirmed: this is exactly
        # what happened on a real run). Wait for the form to actually render
        # first, so the later disappearance check means what it's supposed to.
        try:
            await page.locator('input[name="loginKey"]').first.wait_for(state="visible", timeout=30000)
            print("Login form loaded. Log in by hand now.")
        except PlaywrightTimeoutError:
            print("Login form didn't appear within 30s — page may not have loaded correctly. Waiting anyway...")

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
        print(f"Saved to {PROFILE_DIR} — the app will reuse this profile for shopee_th from now on.")


if __name__ == "__main__":
    asyncio.run(main())
