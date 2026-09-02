from app.db.client import get_supabase


def log_usage(
    api_key_id: str,
    site_id: str,
    request_url: str,
    status: str,
    response_time_ms: int,
) -> str | None:
    supabase = get_supabase()
    result = (
        supabase.table("usage_logs")
        .insert(
            {
                "api_key_id": api_key_id,
                "site_id": site_id,
                "request_url": request_url,
                "status": status,
                "response_time_ms": response_time_ms,
            }
        )
        .execute()
    )
    return result.data[0]["id"] if result.data else None
