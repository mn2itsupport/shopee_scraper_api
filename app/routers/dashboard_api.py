"""JSON aggregates over `usage_logs`, powering the Chart.js charts in
dashboard_pages.py. Aggregation happens client-side in Python, which is fine
at MVP volume; if usage_logs grows large, replace these with a Postgres view
or RPC function and keep the same response shape.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi import Depends, HTTPException

from app.config import settings
from app.core.security import hash_api_key
from app.db.client import get_supabase

router = APIRouter(prefix="/v1/dashboard", tags=["dashboard"])
_basic = HTTPBasic()


def require_admin(credentials: HTTPBasicCredentials = Depends(_basic)) -> None:
    if credentials.password != settings.admin_dashboard_password:
        raise HTTPException(status_code=401, detail="Invalid admin credentials", headers={"WWW-Authenticate": "Basic"})


def _resolve_api_key_id(raw_api_key: str) -> str | None:
    result = (
        get_supabase()
        .table("api_keys")
        .select("id")
        .eq("key_hash", hash_api_key(raw_api_key))
        .limit(1)
        .execute()
    )
    return result.data[0]["id"] if result.data else None


def _fetch_logs(since: datetime, api_key_id: str | None, until: datetime | None = None):
    query = get_supabase().table("usage_logs").select("created_at, status").gte("created_at", since.isoformat())
    if until:
        query = query.lte("created_at", until.isoformat())
    if api_key_id:
        query = query.eq("api_key_id", api_key_id)
    return query.execute().data or []


def _status_counts(logs: list[dict]) -> list[list]:
    counts = Counter(row["status"] for row in logs)
    return [[status, count] for status, count in counts.most_common()]


def _daily_counts(logs: list[dict]) -> list[dict]:
    counts = Counter(row["created_at"][:10] for row in logs)
    return [{"date": day, "count": count} for day, count in sorted(counts.items())]


def _monthly_counts(logs: list[dict]) -> list[dict]:
    counts = Counter(row["created_at"][:7] for row in logs)
    return [{"month": month, "count": count} for month, count in sorted(counts.items())]


@router.get("/stats/daily")
def daily_stats(days: int = Query(30, le=365), admin: None = Depends(require_admin)) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return _daily_counts(_fetch_logs(since, api_key_id=None))


@router.get("/stats/monthly")
def monthly_stats(months: int = Query(12, le=60), admin: None = Depends(require_admin)) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=months * 31)
    return _monthly_counts(_fetch_logs(since, api_key_id=None))


@router.get("/stats/range")
def range_stats(start: datetime, end: datetime, admin: None = Depends(require_admin)) -> list[dict]:
    return _daily_counts(_fetch_logs(start, api_key_id=None, until=end))


@router.get("/stats/status")
def status_stats(
    days: int = Query(7, le=365),
    start: datetime | None = None,
    end: datetime | None = None,
    admin: None = Depends(require_admin),
) -> list[list]:
    since = start or (datetime.now(timezone.utc) - timedelta(days=days))
    return _status_counts(_fetch_logs(since, api_key_id=None, until=end))


def _captcha_rate(logs: list[dict]) -> float:
    if not logs:
        return 0.0
    blocked = sum(1 for row in logs if row["status"] == "captcha_blocked")
    return round(100 * blocked / len(logs), 1)


def _kpi_counts(api_key_id: str | None) -> dict:
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)
    today_logs = _fetch_logs(day_start, api_key_id=api_key_id)
    month_logs = _fetch_logs(month_start, api_key_id=api_key_id)
    return {
        "today": len(today_logs),
        "month": len(month_logs),
        "captcha_rate_today": _captcha_rate(today_logs),
        "captcha_rate_month": _captcha_rate(month_logs),
    }


@router.get("/stats/kpis")
def kpi_stats(admin: None = Depends(require_admin)) -> dict:
    return _kpi_counts(api_key_id=None)


@router.get("/me/daily")
def my_daily_stats(api_key: str, days: int = Query(30, le=365)) -> list[dict]:
    api_key_id = _resolve_api_key_id(api_key)
    if api_key_id is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return _daily_counts(_fetch_logs(since, api_key_id=api_key_id))


@router.get("/me/monthly")
def my_monthly_stats(api_key: str, months: int = Query(12, le=60)) -> list[dict]:
    api_key_id = _resolve_api_key_id(api_key)
    if api_key_id is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    since = datetime.now(timezone.utc) - timedelta(days=months * 31)
    return _monthly_counts(_fetch_logs(since, api_key_id=api_key_id))


@router.get("/me/range")
def my_range_stats(api_key: str, start: datetime, end: datetime) -> list[dict]:
    api_key_id = _resolve_api_key_id(api_key)
    if api_key_id is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return _daily_counts(_fetch_logs(start, api_key_id=api_key_id, until=end))


@router.get("/me/status")
def my_status_stats(
    api_key: str,
    days: int = Query(7, le=365),
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[list]:
    api_key_id = _resolve_api_key_id(api_key)
    if api_key_id is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    since = start or (datetime.now(timezone.utc) - timedelta(days=days))
    return _status_counts(_fetch_logs(since, api_key_id=api_key_id, until=end))


@router.get("/me/kpis")
def my_kpi_stats(api_key: str) -> dict:
    api_key_id = _resolve_api_key_id(api_key)
    if api_key_id is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return _kpi_counts(api_key_id=api_key_id)
