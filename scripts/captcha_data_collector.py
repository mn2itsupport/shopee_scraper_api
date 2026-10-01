"""Standalone CAPTCHA sample collector — for building a YOLO training set
from Shopee's slide/place-the-piece image captcha and its rotate-the-icon
captcha.

Deliberately independent of this repo's app/ package (no app.config,
app.scrapers imports) so it can be dropped into *any* Playwright-based
flow — this project's own scrapers, a one-off pilot script, or a different
project entirely. Only dependency is `playwright`.

What it does when the captcha is on screen: saves a full-page screenshot,
the raw HTML, and a best-effort individual screenshot of every element that
looks like a captcha widget (canvas tags, and img/div elements whose
src/class/id mentions "captcha"/"slide"/"puzzle"/"piece" for the slide type,
or "rotate"/"rotation"/"spin"/"angle" for the rotation type), each into one
timestamped sample folder. It does NOT attempt to solve or classify
anything — it only collects raw material for you to label by hand (or with
a labeling tool) before training. A sample folder can and often will hold
crops for both types at once (webunlocker/solver/slide_solver.py vs.
rotation_solver.py) — that's fine, whichever crop turns out to actually be
the rotation icon or the slide piece gets sorted at labeling time.

Usage as a library, from inside an existing scrape flow, right where your
own captcha detection (e.g. app.scrapers.captcha.is_captcha_page) fires:

    from scripts.captcha_data_collector import collect_captcha_sample

    if await is_captcha_page(page):
        await collect_captcha_sample(page, label="shopee_th")

Usage as a CLI, to grab samples by hand without wiring it into anything:

    python scripts/captcha_data_collector.py --url https://shopee.co.th/some-product -i.123.456

    Opens a real (non-headless) browser, navigates, and waits for you to
    press Enter each time a captcha is showing on screen — so you can
    trigger it manually / solve prior ones out of the way first — and
    saves a sample on every Enter press. Ctrl+C to stop.
"""

from __future__ import annotations

import argparse
import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import ElementHandle, Page

# Heuristic markers for "this element is probably part of a slide/puzzle or
# rotate-the-icon captcha widget" — intentionally broad since we don't know
# Shopee's exact markup ahead of time; false positives just mean an extra
# saved crop, which costs nothing at labeling time. Extend this list as you
# inspect real captured HTML from your own samples.
_PIECE_SELECTORS = [
    "canvas",
    "[class*='captcha' i]",
    "[id*='captcha' i]",
    "[class*='slide' i]",
    "[class*='puzzle' i]",
    "[class*='piece' i]",
    "img[src*='captcha' i]",
    "img[src*='slide' i]",
    # Rotation-captcha markers — separate from the slide ones above since
    # Shopee's rotate widget is a visually distinct challenge (drag a ring
    # to rotate an icon back to upright) and is very unlikely to share the
    # slide widget's class/id naming.
    "[class*='rotate' i]",
    "[id*='rotate' i]",
    "[class*='rotation' i]",
    "[id*='rotation' i]",
    "[class*='spin' i]",
    "[class*='angle' i]",
    "img[src*='rotate' i]",
]

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe(name: str) -> str:
    return _SAFE_NAME.sub("_", name).strip("_") or "sample"


async def _save_element_crop(el: ElementHandle, dest: Path) -> bool:
    try:
        if not await el.is_visible():
            return False
        await el.screenshot(path=str(dest), timeout=3000)
        return True
    except Exception:
        # Zero-size boxes, detached nodes, cross-origin iframes without
        # capture permission, etc. — skip, don't abort the whole sample.
        return False


async def collect_captcha_sample(page: Page, out_dir: str | Path = "captcha_dataset", label: str = "unknown") -> Path:
    """Saves one dataset sample (full-page screenshot, HTML, and any
    element crops that match `_PIECE_SELECTORS`) into a fresh timestamped
    subfolder of `out_dir`. Safe to call speculatively — even if nothing
    matches the piece selectors, the full-page screenshot and HTML are
    still saved so you can review and extend the selector list later.

    Returns the created sample folder's path.
    """
    root = Path(out_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    sample_dir = root / f"{stamp}_{_safe(label)}"
    sample_dir.mkdir(parents=True, exist_ok=True)

    await page.screenshot(path=str(sample_dir / "full_page.png"), full_page=True)
    (sample_dir / "page.html").write_text(await page.content(), encoding="utf-8")
    (sample_dir / "url.txt").write_text(page.url, encoding="utf-8")

    seen_boxes: set[tuple[float, float, float, float]] = set()
    crop_count = 0
    for selector in _PIECE_SELECTORS:
        try:
            elements = await page.query_selector_all(selector)
        except Exception:
            continue
        for el in elements:
            box = await el.bounding_box()
            if box is None:
                continue
            key = (box["x"], box["y"], box["width"], box["height"])
            if key in seen_boxes:
                continue  # same element matched by more than one selector
            seen_boxes.add(key)
            dest = sample_dir / f"crop_{crop_count:02d}_{_safe(selector)}.png"
            if await _save_element_crop(el, dest):
                crop_count += 1

    return sample_dir


async def _run_cli(url: str, out_dir: str, label: str) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        page = await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded")

        print("Browser open. Navigate/trigger the captcha as needed.")
        print("Press Enter to save a sample of whatever is on screen right now, Ctrl+C to quit.")
        try:
            while True:
                await asyncio.get_event_loop().run_in_executor(None, input)
                sample_dir = await collect_captcha_sample(page, out_dir=out_dir, label=label)
                print(f"saved -> {sample_dir}")
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", required=True, help="Page to open (a Shopee PDP or category page likely to trigger the captcha)")
    parser.add_argument("--out", default="captcha_dataset", help="Output directory for samples (default: ./captcha_dataset)")
    parser.add_argument("--label", default="shopee", help="Short tag included in each sample folder name (e.g. shopee_th)")
    args = parser.parse_args()
    asyncio.run(_run_cli(args.url, args.out, args.label))


if __name__ == "__main__":
    main()
