import logging

from postgrest.exceptions import APIError

from app.db.client import get_supabase

logger = logging.getLogger(__name__)

# Keeps one runaway exception message from bloating usage_logs.
_MAX_ERROR_CHARS = 500

# Flipped off the first time the insert reports usage_logs.error_message as
# an unknown column, i.e. the migration in schema.sql hasn't been applied
# yet — from then on rows are written exactly as before the column existed.
_error_column_available = True


def _is_missing_column_error(exc: APIError) -> bool:
    # PGRST204: "Could not find the '<col>' column of '<table>' in the schema cache"
    return exc.code == "PGRST204" or "error_message" in (exc.message or "")


def log_usage(
    api_key_id: str,
    site_id: str,
    request_url: str,
    status: str,
    response_time_ms: int,
    error_message: str | None = None,
) -> str | None:
    global _error_column_available

    row = {
        "api_key_id": api_key_id,
        "site_id": site_id,
        "request_url": request_url,
        "status": status,
        "response_time_ms": response_time_ms,
    }
    if error_message and _error_column_available:
        row["error_message"] = error_message[:_MAX_ERROR_CHARS]

    try:
        result = get_supabase().table("usage_logs").insert(row).execute()
    except APIError as exc:
        if "error_message" not in row or not _is_missing_column_error(exc):
            raise
        _error_column_available = False
        logger.warning("usage_logs.error_message column missing; apply the migration in app/db/schema.sql to record failure reasons")
        del row["error_message"]
        result = get_supabase().table("usage_logs").insert(row).execute()

    return result.data[0]["id"] if result.data else None
