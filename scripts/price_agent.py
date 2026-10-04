"""Price agent for shopee_th: runs on the machine that has the real,
logged-in Chrome (scripts/launch_real_chrome_th.ps1) and serves
GET /price?url=<product url> to the Railway-hosted API.

    .venv\\Scripts\\python.exe -m uvicorn scripts.price_agent:app --host 127.0.0.1 --port 8765

Needs in .env on this machine: SHOPEE_TH_REAL_CHROME_CDP_URL (default
http://127.0.0.1:9222) and SHOPEE_TH_PRICE_AGENT_TOKEN (shared secret; the
API sends it as X-Agent-Token). Expose it to Railway through a tunnel
(Cloudflare Tunnel / Tailscale Funnel) — never expose Chrome's CDP port
itself, it grants full control of the browser.
"""

import asyncio
import hmac
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from fastapi import FastAPI, Header, HTTPException  # noqa: E402

from app.config import settings  # noqa: E402
from app.scrapers.real_chrome import capture_get_pc  # noqa: E402

logger = logging.getLogger("price_agent")
app = FastAPI(title="shopee_th price agent")
# One tab at a time: a real Chrome session shouldn't look like a burst of
# parallel automated navigations.
_lock = asyncio.Semaphore(1)


@app.get("/price")
async def price(url: str, x_agent_token: str = Header(default="")) -> dict:
    expected = settings.shopee_th_price_agent_token
    if not expected or not hmac.compare_digest(x_agent_token, expected):
        raise HTTPException(status_code=401, detail="bad agent token")
    if "shopee.co.th" not in url:
        raise HTTPException(status_code=400, detail="shopee.co.th URLs only")
    cdp_url = settings.shopee_th_real_chrome_cdp_url or "http://127.0.0.1:9222"
    async with _lock:
        try:
            body = await capture_get_pc(cdp_url, url, settings.shopee_th_price_probe_timeout_seconds)
        except Exception as exc:
            logger.warning("capture failed for %s: %s", url, exc)
            raise HTTPException(status_code=502, detail=f"chrome capture failed: {type(exc).__name__}")
    return body


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/status")
async def status() -> dict:
    """Polled by the API's price-agent monitor: unlike /health (which the
    local watchdog uses to decide whether to restart this process), it also
    reports whether Chrome itself is reachable over CDP."""
    cdp_url = settings.shopee_th_real_chrome_cdp_url or "http://127.0.0.1:9222"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            (await client.get(f"{cdp_url.rstrip('/')}/json/version")).raise_for_status()
        chrome = True
    except Exception:
        chrome = False
    return {"ok": chrome, "chrome": chrome, "busy": _lock.locked()}
