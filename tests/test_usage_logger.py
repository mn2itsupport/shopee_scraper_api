"""log_usage records failure reasons, and keeps logging when the
usage_logs.error_message migration hasn't been applied yet."""

import os

import pytest
from postgrest.exceptions import APIError

os.environ.setdefault("SUPABASE_URL", "http://example.invalid")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from app.core import usage_logger  # noqa: E402


class _FakeTable:
    def __init__(self, db):
        self.db = db

    def insert(self, row):
        self.row = row
        return self

    def execute(self):
        if "error_message" in self.row and not self.db.has_error_column:
            raise APIError({"code": "PGRST204", "message": "Could not find the 'error_message' column of 'usage_logs' in the schema cache"})
        self.db.rows.append(dict(self.row))
        return type("R", (), {"data": [{"id": f"log-{len(self.db.rows)}"}]})()


class _FakeDB:
    def __init__(self, has_error_column):
        self.has_error_column, self.rows = has_error_column, []

    def table(self, name):
        assert name == "usage_logs"
        return _FakeTable(self)


@pytest.fixture
def db(monkeypatch, request):
    fake = _FakeDB(has_error_column=getattr(request, "param", True))
    monkeypatch.setattr(usage_logger, "get_supabase", lambda: fake)
    monkeypatch.setattr(usage_logger, "_error_column_available", True)
    return fake


def test_failure_reason_is_stored_and_truncated(db):
    assert usage_logger.log_usage("k", "s", "u", "failed", 10, "boom " * 200) == "log-1"
    assert db.rows[0]["error_message"].startswith("boom")
    assert len(db.rows[0]["error_message"]) == 500


def test_success_row_has_no_error_field(db):
    usage_logger.log_usage("k", "s", "u", "success", 10)
    assert "error_message" not in db.rows[0]


@pytest.mark.parametrize("db", [False], indirect=True)
def test_missing_column_falls_back_and_stops_trying(db):
    assert usage_logger.log_usage("k", "s", "u", "failed", 10, "boom") == "log-1"
    assert db.rows == [{"api_key_id": "k", "site_id": "s", "request_url": "u", "status": "failed", "response_time_ms": 10}]
    assert usage_logger._error_column_available is False

    usage_logger.log_usage("k", "s", "u", "failed", 10, "boom again")
    assert len(db.rows) == 2 and "error_message" not in db.rows[1]


def test_unrelated_insert_errors_still_raise(db, monkeypatch):
    def broken():
        raise APIError({"code": "23514", "message": "violates check constraint"})

    monkeypatch.setattr(_FakeTable, "execute", lambda self: broken())
    with pytest.raises(APIError):
        usage_logger.log_usage("k", "s", "u", "bogus", 10, "boom")
