"""Mobile-web price agent for shopee_th: same HTTP contract as
scripts/price_agent.py (GET /price?url=… with X-Agent-Token → the raw PDP
JSON body), but answers from pdp/get_rw via the phone-emulating Chrome on
port 9223. Runs beside the desktop agent, on its own port.

    .venv\\Scripts\\python.exe -m uvicorn shopee_th_mobile.agent:app --host 127.0.0.1 --port 8766

Needs in .env: SHOPEE_TH_MOBILE_AGENT_TOKEN (shared secret). Optional:
SHOPEE_TH_MOBILE_CDP_URL, SHOPEE_TH_MOBILE_TIMEOUT_SECONDS,
SHOPEE_TH_MOBILE_MIN_INTERVAL_SECONDS, SHOPEE_TH_MOBILE_BLOCK_COOLDOWN_SECONDS.
Never expose port 9223 itself — CDP grants full control of the browser.
"""

import hmac
import logging
import os

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from shopee_th_mobile import capture

logger = logging.getLogger("shopee_th_mobile.agent")
app = FastAPI(title="shopee_th mobile price agent")


@app.get("/price")
async def price(url: str, x_agent_token: str = Header(default="")):
    expected = os.getenv("SHOPEE_TH_MOBILE_AGENT_TOKEN", "")
    if not expected or not hmac.compare_digest(x_agent_token, expected):
        raise HTTPException(status_code=401, detail="bad agent token")
    if "shopee.co.th" not in url:
        raise HTTPException(status_code=400, detail="shopee.co.th URLs only")
    try:
        endpoint, body = await capture.capture_get_rw(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except (capture.BlockedError, capture.CoolingDownError) as exc:
        logger.warning("%s", exc)
        raise HTTPException(status_code=429, detail=str(exc),
                            headers={"Retry-After": str(capture.cooldown_remaining())})
    except Exception as exc:
        logger.warning("capture failed for %s: %s", url, exc)
        raise HTTPException(status_code=502, detail=f"chrome capture failed: {type(exc).__name__}")
    return JSONResponse(body, headers={"X-Shopee-Endpoint": endpoint})


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/status")
async def status() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            (await client.get(f"{capture.CDP_URL.rstrip('/')}/json/version")).raise_for_status()
        chrome = True
    except Exception:
        chrome = False
    cooldown = capture.cooldown_remaining()
    return {"ok": chrome and not cooldown, "chrome": chrome, "busy": capture._lock.locked(),
            "blocked_cooldown_seconds": cooldown}
