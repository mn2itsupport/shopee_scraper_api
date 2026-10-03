"""Cookie session for the customer-facing web UI (/login, /app/*).

There are no user accounts in this system — a client *is* an API key — so
"signing in" means presenting a valid key once; it is then kept in an
HttpOnly cookie and re-validated against `api_keys` on every page load, so a
key that is revoked or expires stops working in the UI immediately.
"""

from datetime import datetime, timezone

from fastapi import Request

from app.core.auth import _parse_timestamptz
from app.core.security import hash_api_key
from app.db.client import get_supabase

COOKIE_NAME = "hv_key"
COOKIE_MAX_AGE = 30 * 24 * 3600

_SELECT = (
    "id, client_id, plan_id, status, expires_at, key_prefix, created_at, "
    "clients(name, email), "
    "plans(name, requests_per_minute, daily_quota, monthly_quota, duration_days, price_cents)"
)


def check_key(raw_key: str | None) -> tuple[dict | None, str | None]:
    """Returns (api_keys row with client/plan joined, None) or (None, reason)."""
    raw_key = (raw_key or "").strip()
    if not raw_key:
        return None, "Enter your API key."

    rows = get_supabase().table("api_keys").select(_SELECT).eq("key_hash", hash_api_key(raw_key)).limit(1).execute().data
    if not rows:
        return None, "That API key was not recognised."

    row = rows[0]
    if row["status"] != "active":
        return None, "This API key is suspended."

    expires_at = _parse_timestamptz(row["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        return None, "This API key has expired."

    return row, None


def session_from_request(request: Request) -> dict | None:
    row, _ = check_key(request.cookies.get(COOKIE_NAME))
    return row
