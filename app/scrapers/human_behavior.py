"""Human-like interaction simulation for Playwright/Patchright pages.

Anti-bot vendors don't only fingerprint the browser/TLS/proxy layer (that's
what browser_pool.py's _BrowserProfile/Stealth/Patchright stack targets) —
they also score *behavioral* signal: does the mouse ever move, does the
cursor teleport straight to a coordinate, does scrolling happen in one
instant jump vs. a human's uneven, paused, sometimes-overshooting motion,
does the page get any dwell time before data is read out of it. A script
that navigates and immediately reads the DOM produces zero mouse events and
an unnaturally flat timing profile — this module exists to close that gap
for the two navigations ShopeeScraper.fetch_pdp already does (home page
warm-up, product page).

Deliberately best-effort: every function here swallows its own failures
(a closed page, a race with navigation) rather than let a cosmetic
simulation step fail the actual scrape.
"""

import asyncio
import logging
import random

from playwright.async_api import Page

logger = logging.getLogger(__name__)


async def _safe(coro) -> None:
    try:
        await coro
    except Exception:
        logger.debug("human_behavior step skipped (page likely mid-navigation)", exc_info=True)


def _bezier_point(p0: tuple[float, float], p1: tuple[float, float], p2: tuple[float, float], t: float) -> tuple[float, float]:
    x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t**2 * p2[0]
    y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t**2 * p2[1]
    return x, y


async def human_mouse_move(page: Page, target_x: float | None = None, target_y: float | None = None) -> None:
    """Moves the mouse along a curved, multi-step path instead of Playwright's
    default straight-line/instant move — a real trackpad/mouse rarely travels
    in a perfectly straight line at constant speed, and jumping straight to a
    coordinate with a single move() call is one of the easier "no human
    present" signals to check for.
    """
    viewport = page.viewport_size or {"width": 1366, "height": 768}
    start = (random.uniform(0, viewport["width"]), random.uniform(0, viewport["height"]))
    end = (
        target_x if target_x is not None else random.uniform(0, viewport["width"]),
        target_y if target_y is not None else random.uniform(0, viewport["height"]),
    )
    # Control point offset to the side of the straight line, so the path
    # bows out like a real hand movement instead of being perfectly linear.
    control = (
        (start[0] + end[0]) / 2 + random.uniform(-120, 120),
        (start[1] + end[1]) / 2 + random.uniform(-120, 120),
    )

    steps = random.randint(15, 30)
    async def _move():
        for i in range(1, steps + 1):
            x, y = _bezier_point(start, control, end, i / steps)
            await page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.005, 0.02))

    await _safe(_move())


async def human_scroll(page: Page, total_distance: int | None = None) -> None:
    """Scrolls down the page in several uneven increments with pauses in
    between (like a person reading product images/description) instead of
    one instant jump to a target scroll offset. Occasionally scrolls back up
    a little, mimicking re-reading something just passed.
    """
    if total_distance is None:
        total_distance = random.randint(600, 2200)

    remaining = total_distance
    async def _scroll():
        nonlocal remaining
        while remaining > 0:
            step = min(remaining, random.randint(120, 350))
            await page.mouse.wheel(0, step)
            remaining -= step
            await asyncio.sleep(random.uniform(0.15, 0.55))
            if random.random() < 0.12:
                # Small backtrack — re-reading, not a monotonic sweep.
                backtrack = random.randint(40, 150)
                await page.mouse.wheel(0, -backtrack)
                await asyncio.sleep(random.uniform(0.1, 0.3))

    await _safe(_scroll())


async def human_idle_pause(min_seconds: float = 0.4, max_seconds: float = 1.6) -> None:
    """A plain randomized pause — real page dwell time before any action,
    never a fixed sleep duration a timing analysis could key off.
    """
    await asyncio.sleep(random.uniform(min_seconds, max_seconds))


async def simulate_human_browsing(page: Page, *, scroll: bool = True, hover_selector: str = "") -> None:
    """Orchestrates a short, randomized sequence of the primitives above —
    call this once per navigation (home-page warm-up, product page) after
    the DOM has settled, before reading data out of the page. Never raises;
    a failure here is cosmetic and shouldn't affect the real scrape.
    """
    try:
        await human_idle_pause(0.3, 0.9)
        await human_mouse_move(page)

        if hover_selector:
            try:
                locator = page.locator(hover_selector).first
                box = await locator.bounding_box(timeout=1000)
                if box:
                    await human_mouse_move(
                        page,
                        box["x"] + box["width"] * random.uniform(0.3, 0.7),
                        box["y"] + box["height"] * random.uniform(0.3, 0.7),
                    )
            except Exception:
                pass

        if scroll:
            await human_scroll(page)
            await human_idle_pause(0.2, 0.7)
            await human_mouse_move(page)
    except Exception:
        logger.debug("simulate_human_browsing failed; continuing scrape unaffected", exc_info=True)
