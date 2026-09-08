"""Import cookies exported from a REAL, non-automated browser session into
shopee_th's persistent profile (browser_profiles/shopee_th/) — an
alternative to scripts/shopee_th_manual_login.py, for when even a human
typing credentials into a Playwright-launched (CDP-controlled) browser gets
Shopee TH's own traffic-verification wall (confirmed via live testing: it
rejects the login regardless of headless/headed once Playwright/CDP is
driving the browser at all, human-typed or not).

How to get the export file:
  1. In your OWN everyday Chrome (no devtools/automation attached), log into
     https://shopee.co.th/buyer/login normally.
  2. Export its cookies to JSON — e.g. with the "Cookie-Editor" extension:
     open it on a shopee.co.th tab, click Export, choose JSON, save the file.
  3. Run this script pointing at that file:
         python scripts/shopee_th_import_cookies.py path\\to\\export.json

Tolerates a few common export shapes (Cookie-Editor, EditThisCookie, and
Playwright's own storage_state format) — see _normalize_cookie() below.
"""

import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

PROFILE_DIR = Path(__file__).resolve().parent.parent / "browser_profiles" / "shopee_th"

_SAME_SITE_MAP = {
    "no_restriction": "None",
    "unspecified": "Lax",
    "lax": "Lax",
    "strict": "Strict",
    "none": "None",
}


def _normalize_cookie(raw: dict) -> dict | None:
    name = raw.get("name")
    value = raw.get("value")
    domain = raw.get("domain")
    if not name or value is None or not domain:
        return None

    cookie = {
        "name": name,
        "value": value,
        "domain": domain,
        "path": raw.get("path", "/"),
    }

    if raw.get("session"):
        cookie["expires"] = -1
    else:
        expires = raw.get("expirationDate", raw.get("expires"))
        if expires is not None:
            cookie["expires"] = float(expires)

    if "httpOnly" in raw:
        cookie["httpOnly"] = bool(raw["httpOnly"])
    if "secure" in raw:
        cookie["secure"] = bool(raw["secure"])

    same_site_raw = str(raw.get("sameSite", "")).lower()
    if same_site_raw in _SAME_SITE_MAP:
        cookie["sameSite"] = _SAME_SITE_MAP[same_site_raw]

    return cookie


async def main(export_path: Path) -> None:
    raw_cookies = json.loads(export_path.read_text(encoding="utf-8"))
    # Playwright's own storage_state format nests cookies under a key.
    if isinstance(raw_cookies, dict) and "cookies" in raw_cookies:
        raw_cookies = raw_cookies["cookies"]

    cookies = [c for c in (_normalize_cookie(r) for r in raw_cookies) if c is not None]
    shopee_cookies = [c for c in cookies if "shopee" in c["domain"]]
    print(f"Parsed {len(cookies)} cookies from {export_path} ({len(shopee_cookies)} are shopee.* domains)")
    if not shopee_cookies:
        print("No shopee.* cookies found in this export — nothing to import.")
        return

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=True,
            locale="th-TH",
        )
        await context.add_cookies(shopee_cookies)
        await context.close()

    print(f"Imported {len(shopee_cookies)} shopee.* cookies into {PROFILE_DIR}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/shopee_th_import_cookies.py <path-to-exported-cookies.json>")
        sys.exit(1)
    asyncio.run(main(Path(sys.argv[1])))
