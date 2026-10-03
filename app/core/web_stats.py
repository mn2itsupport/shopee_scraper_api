"""Per-key numbers for the /app overview page, computed from `usage_logs`.

Same approach as routers/dashboard_api.py: aggregate in Python, which is fine
at MVP volume; move to a Postgres view/RPC if usage_logs grows large.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from app.core.auth import _parse_timestamptz
from app.db.client import get_supabase

_LOG_LIMIT = 20000


def _ago(then: datetime, now: datetime) -> str:
    seconds = int((now - then).total_seconds())
    if seconds < 60:
        return "Just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} d ago"


def _count_since(api_key_id: str, since: datetime) -> int:
    result = (
        get_supabase()
        .table("usage_logs")
        .select("id", count="exact")
        .eq("api_key_id", api_key_id)
        .gte("created_at", since.isoformat())
        .execute()
    )
    return result.count or 0


def quota_usage(api_key_id: str) -> dict:
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "today": _count_since(api_key_id, day_start),
        "month": _count_since(api_key_id, day_start.replace(day=1)),
    }


def overview(api_key_id: str, days: int = 7) -> dict:
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    since = day_start - timedelta(days=days - 1)

    logs = (
        get_supabase()
        .table("usage_logs")
        .select("created_at, status, response_time_ms, request_url, sites(site_key)")
        .eq("api_key_id", api_key_id)
        .gte("created_at", since.isoformat())
        .order("created_at", desc=True)
        .limit(_LOG_LIMIT)
        .execute()
        .data
        or []
    )

    total = len(logs)
    successes = [row for row in logs if row["status"] == "success"]
    latencies = [row["response_time_ms"] for row in successes if row.get("response_time_ms") is not None]

    per_day = Counter(row["created_at"][:10] for row in logs)
    buckets = []
    for offset in range(days):
        day = (since + timedelta(days=offset)).date()
        buckets.append({"day": day.strftime("%a"), "count": per_day.get(day.isoformat(), 0)})
    peak = max((b["count"] for b in buckets), default=0) or 1
    for b in buckets:
        b["height"] = max(4, round(b["count"] / peak * 150)) if b["count"] else 2

    per_domain: dict[str, list[int]] = {}
    for row in logs:
        host = urlparse(row["request_url"]).hostname or "unknown"
        entry = per_domain.setdefault(host, [0, 0])
        entry[0] += 1
        entry[1] += 1 if row["status"] == "success" else 0
    domains = [
        {"name": host, "count": n, "rate": f"{round(100 * ok / n, 1)}%"}
        for host, (n, ok) in sorted(per_domain.items(), key=lambda item: -item[1][0])[:5]
    ]

    chips = {
        "success": ("Success", "ok"),
        "failed": ("Failed", "bad"),
        "captcha_blocked": ("Captcha", "warn"),
        "rate_limited": ("Rate limited", "warn"),
        "quota_exceeded": ("Quota", "warn"),
    }
    recent = []
    for row in logs[:8]:
        label, tone = chips.get(row["status"], (row["status"], "neutral"))
        host = urlparse(row["request_url"]).hostname or row["request_url"]
        recent.append(
            {
                "when": _ago(_parse_timestamptz(row["created_at"]), now),
                "site": (row.get("sites") or {}).get("site_key", "—"),
                "url": host + (urlparse(row["request_url"]).path[:28] or ""),
                "result": label,
                "tone": tone,
                "time": f"{row['response_time_ms'] / 1000:.1f} s" if row.get("response_time_ms") is not None else "—",
            }
        )

    return {
        "days": days,
        "total": total,
        "domain_count": len(per_domain),
        "success_rate": f"{round(100 * len(successes) / total, 1)}%" if total else "—",
        "avg_latency": f"{sum(latencies) / len(latencies) / 1000:.1f} s" if latencies else "—",
        "buckets": buckets,
        "domains": domains,
        "recent": recent,
        "truncated": total >= _LOG_LIMIT,
    }
