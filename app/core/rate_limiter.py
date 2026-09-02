"""Two layers of protection against overuse:

1. Burst limiter — in-memory sliding window per API key. Cheap, no DB round trip,
   catches "client hammering us every 100ms". Resets on process restart; for a
   multi-instance deployment, swap the in-memory dict for Redis (INCR + EXPIRE)
   without changing the call site below.
2. Quota limiter — reads today's/this month's row count straight from `usage_logs`
   (the same table the dashboard reads), so it stays correct across restarts and
   multiple instances without needing a separate counter table.
"""

import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import HTTPException

from app.db.client import get_supabase

_windows: dict[str, deque[float]] = defaultdict(deque)
_WINDOW_SECONDS = 60


def check_burst_limit(api_key_id: str, requests_per_minute: int) -> None:
    now = time.monotonic()
    window = _windows[api_key_id]

    while window and now - window[0] > _WINDOW_SECONDS:
        window.popleft()

    if len(window) >= requests_per_minute:
        raise HTTPException(status_code=429, detail="Rate limit exceeded, slow down")

    window.append(now)


def check_quota(api_key_id: str, daily_quota: int, monthly_quota: int) -> None:
    supabase = get_supabase()
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)

    daily_count = (
        supabase.table("usage_logs")
        .select("id", count="exact")
        .eq("api_key_id", api_key_id)
        .gte("created_at", day_start.isoformat())
        .execute()
        .count
        or 0
    )
    if daily_count >= daily_quota:
        raise HTTPException(status_code=429, detail="Daily quota exceeded")

    monthly_count = (
        supabase.table("usage_logs")
        .select("id", count="exact")
        .eq("api_key_id", api_key_id)
        .gte("created_at", month_start.isoformat())
        .execute()
        .count
        or 0
    )
    if monthly_count >= monthly_quota:
        raise HTTPException(status_code=429, detail="Monthly quota exceeded")
