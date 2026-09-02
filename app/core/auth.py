import asyncio
from datetime import datetime, timezone

from fastapi import Header, HTTPException

from app.core.security import hash_api_key
from app.db.client import get_supabase
from app.models.schemas import AuthedKey


def _lookup_key(key_hash: str) -> dict | None:
    result = (
        get_supabase()
        .table("api_keys")
        .select("id, client_id, plan_id, status, expires_at, plans(requests_per_minute, daily_quota, monthly_quota)")
        .eq("key_hash", key_hash)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def _mark_expired(api_key_id: str) -> None:
    get_supabase().table("api_keys").update({"status": "expired"}).eq("id", api_key_id).execute()


async def require_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> AuthedKey:
    # Supabase's client is synchronous (blocking network I/O); run it off the
    # event loop so one slow lookup doesn't stall every other in-flight request.
    row = await asyncio.to_thread(_lookup_key, hash_api_key(x_api_key))

    if row is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    expires_at = datetime.fromisoformat(row["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if row["status"] != "active":
        raise HTTPException(status_code=403, detail="API key is suspended")

    if expires_at < datetime.now(timezone.utc):
        # Subscription lapsed: flip status so future lookups short-circuit on the cheaper check above.
        await asyncio.to_thread(_mark_expired, row["id"])
        raise HTTPException(status_code=403, detail="Subscription has expired")

    plan = row["plans"]
    return AuthedKey(
        api_key_id=row["id"],
        client_id=row["client_id"],
        plan_id=row["plan_id"],
        requests_per_minute=plan["requests_per_minute"],
        daily_quota=plan["daily_quota"],
        monthly_quota=plan["monthly_quota"],
    )
