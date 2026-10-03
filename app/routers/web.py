"""Customer-facing web UI: landing page, key sign-in, and the /app pages
(overview, playground, keys & plan). Everything is server-rendered Jinja over
data this app already has — `api_keys`, `plans`, `usage_logs`, `sites` — and
the same `/v1/{site}/pdp` pipeline the public API uses, so no new tables.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.requests import Request

from app.config import settings
from app.core import web_stats
from app.core.auth import _parse_timestamptz
from app.core.security import generate_api_key, hash_api_key, key_prefix
from app.core.web_auth import COOKIE_MAX_AGE, COOKIE_NAME, check_key, session_from_request
from app.db.client import get_supabase
from app.models.schemas import AuthedKey, ScrapeRequest
from app.routers.scrape import scrape_pdp

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
templates.env.globals["product_name"] = settings.product_name
templates.env.globals["contact_email"] = settings.contact_email

MAX_KEYS_PER_CLIENT = 5


def _fmt_date(value: str | None) -> str:
    if not value:
        return "—"
    return _parse_timestamptz(value).strftime("%b %d, %Y")


def _price_label(price_cents: int) -> str:
    if not price_cents:
        return "Custom"
    dollars = price_cents / 100
    return f"${dollars:,.0f}" if dollars == int(dollars) else f"${dollars:,.2f}"


def _render(request: Request, name: str, context: dict, status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _login_redirect(request: Request) -> RedirectResponse:
    return RedirectResponse(f"/login?next={request.url.path}", status_code=303)


def _safe_next(value: str | None) -> str:
    # Only ever redirect back into /app/* — never to an arbitrary URL.
    return value if value and value.startswith("/app") and "//" not in value else "/app"


def _app_context(session: dict, active: str, **extra) -> dict:
    plan = session["plans"]
    usage = web_stats.quota_usage(session["id"])
    return {
        "active": active,
        "client_name": (session.get("clients") or {}).get("name", ""),
        "plan_name": plan["name"],
        "plan_daily": plan["daily_quota"],
        "month_used": usage["month"],
        "month_quota": plan["monthly_quota"],
        "month_pct": min(100, round(100 * usage["month"] / plan["monthly_quota"])) if plan["monthly_quota"] else 0,
        "usage": usage,
        **extra,
    }


# ---------------------------------------------------------------- public pages


@router.get("/", response_class=HTMLResponse)
def landing(request: Request) -> HTMLResponse:
    plans = []
    try:
        rows = get_supabase().table("plans").select("*").order("price_cents").execute().data or []
        plans = [
            {
                "name": row["name"],
                "price": _price_label(row["price_cents"]),
                "has_price": bool(row["price_cents"]),
                "rpm": row["requests_per_minute"],
                "daily": f"{row['daily_quota']:,}",
                "monthly": f"{row['monthly_quota']:,}",
                "days": row["duration_days"],
            }
            for row in rows
        ]
    except Exception:  # landing page must render even if the DB is unreachable
        plans = []
    return _render(request, "web/landing.html", {"plans": plans, "max_batch": settings.max_batch_size})


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str | None = None) -> HTMLResponse:
    if session_from_request(request):
        return RedirectResponse(_safe_next(next), status_code=303)
    return _render(request, "web/login.html", {"error": None, "next": _safe_next(next)})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    # application/x-www-form-urlencoded parsed by hand: avoids adding a
    # python-multipart dependency just for one two-field form.
    fields = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    api_key = (fields.get("api_key") or [""])[0]
    next_url = _safe_next((fields.get("next") or [""])[0])

    row, error = await asyncio.to_thread(check_key, api_key)
    if row is None:
        return _render(request, "web/login.html", {"error": error, "next": next_url}, status_code=401)

    response = RedirectResponse(next_url, status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        api_key.strip(),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@router.post("/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


# ------------------------------------------------------------------ app pages


@router.get("/app", response_class=HTMLResponse)
def app_overview(request: Request):
    session = session_from_request(request)
    if session is None:
        return _login_redirect(request)
    stats = web_stats.overview(session["id"])
    return _render(request, "web/overview.html", _app_context(session, "overview", stats=stats))


@router.get("/app/playground", response_class=HTMLResponse)
def app_playground(request: Request):
    session = session_from_request(request)
    if session is None:
        return _login_redirect(request)
    sites = get_supabase().table("sites").select("site_key, display_name").eq("is_active", True).order("site_key").execute().data
    return _render(request, "web/playground.html", _app_context(session, "playground", sites=sites))


@router.get("/app/keys", response_class=HTMLResponse)
def app_keys(request: Request):
    session = session_from_request(request)
    if session is None:
        return _login_redirect(request)

    rows = (
        get_supabase()
        .table("api_keys")
        .select("id, key_prefix, status, created_at, expires_at")
        .eq("client_id", session["client_id"])
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )
    keys = [
        {
            "id": row["id"],
            "prefix": row["key_prefix"],
            "status": row["status"].capitalize(),
            "active": row["status"] == "active",
            "created": _fmt_date(row["created_at"]),
            "expires": _fmt_date(row["expires_at"]),
            "current": row["id"] == session["id"],
        }
        for row in rows
    ]
    plan = session["plans"]
    return _render(
        request,
        "web/keys.html",
        _app_context(
            session,
            "keys",
            keys=keys,
            plan=plan,
            plan_price=_price_label(plan["price_cents"]),
            expires=_fmt_date(session["expires_at"]),
            can_create=sum(1 for k in keys if k["active"]) < MAX_KEYS_PER_CLIENT,
            max_keys=MAX_KEYS_PER_CLIENT,
        ),
    )


# ------------------------------------------------------------- JSON endpoints


class PlaygroundRequest(BaseModel):
    site_key: str
    url: str


@router.post("/app/api/scrape")
async def app_scrape(body: PlaygroundRequest, request: Request) -> JSONResponse:
    """Runs the exact same pipeline as POST /v1/{site}/pdp (rate limit, quota,
    scrape, usage log) using the signed-in key, so playground requests count
    against the plan like any other request."""
    session = await asyncio.to_thread(session_from_request, request)
    if session is None:
        raise HTTPException(status_code=401, detail="Not signed in")

    plan = session["plans"]
    key = AuthedKey(
        api_key_id=session["id"],
        client_id=session["client_id"],
        plan_id=session["plan_id"],
        requests_per_minute=plan["requests_per_minute"],
        daily_quota=plan["daily_quota"],
        monthly_quota=plan["monthly_quota"],
    )
    result = await scrape_pdp(body.site_key, ScrapeRequest(url=body.url), key)
    return JSONResponse(result)


@router.post("/app/api/keys")
def app_create_key(request: Request) -> JSONResponse:
    session = session_from_request(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Not signed in")

    supabase = get_supabase()
    active = (
        supabase.table("api_keys").select("id", count="exact").eq("client_id", session["client_id"]).eq("status", "active").execute().count
        or 0
    )
    if active >= MAX_KEYS_PER_CLIENT:
        raise HTTPException(status_code=409, detail=f"Key limit reached ({MAX_KEYS_PER_CLIENT} active keys)")

    raw_key = generate_api_key()
    expires_at = datetime.now(timezone.utc) + timedelta(days=session["plans"]["duration_days"])
    supabase.table("api_keys").insert(
        {
            "client_id": session["client_id"],
            "plan_id": session["plan_id"],
            "key_hash": hash_api_key(raw_key),
            "key_prefix": key_prefix(raw_key),
            "expires_at": expires_at.isoformat(),
        }
    ).execute()
    # The raw key is returned exactly once; only its hash is stored.
    return JSONResponse({"api_key": raw_key})


@router.post("/app/api/keys/{key_id}/revoke")
def app_revoke_key(key_id: str, request: Request) -> JSONResponse:
    session = session_from_request(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    if key_id == session["id"]:
        raise HTTPException(status_code=409, detail="You cannot revoke the key you are signed in with")

    result = (
        get_supabase()
        .table("api_keys")
        .update({"status": "suspended"})
        .eq("id", key_id)
        .eq("client_id", session["client_id"])  # only ever this client's own keys
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Key not found")
    return JSONResponse({"ok": True})
