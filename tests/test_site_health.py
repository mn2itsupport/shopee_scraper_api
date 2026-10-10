"""Per-site success-rate aggregation behind GET /v1/dashboard/stats/sites."""

import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SUPABASE_URL", "http://example.invalid")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from app.config import settings  # noqa: E402
from app.routers import dashboard_api  # noqa: E402

BR = {"site_key": "shopee_br", "display_name": "Shopee Brazil"}
TH = {"site_key": "shopee_th", "display_name": "Shopee Thailand"}


def _log(status, site, ms=None):
    return {"status": status, "response_time_ms": ms, "sites": site}


def test_success_rate_excludes_pre_scrape_rejections():
    logs = [
        _log("success", BR, 1000),
        _log("failed", BR, 5000),
        _log("rate_limited", BR),
        _log("quota_exceeded", BR),
    ]
    [br] = dashboard_api._site_health(logs)
    assert br["attempts"] == 2
    assert br["success_rate"] == 50.0
    assert br["avg_ms"] == 1000  # failed request's latency not counted


def test_sites_sorted_worst_first_with_latency_percentiles():
    logs = [_log("success", BR, ms) for ms in range(100, 2100, 100)]  # 20 successes
    logs += [_log("success", TH, 3000), _log("captcha_blocked", TH, 9000), _log("failed", TH)]
    th, br = dashboard_api._site_health(logs)
    assert th["site_key"] == "shopee_th"
    assert th["success_rate"] == 33.3
    assert (th["success"], th["failed"], th["captcha_blocked"]) == (1, 1, 1)
    assert br["success_rate"] == 100.0
    assert br["avg_ms"] == 1050
    assert br["p95_ms"] == 1900


def test_missing_site_join_is_grouped_as_unknown():
    [row] = dashboard_api._site_health([_log("success", None, 10)])
    assert row["site_key"] == "unknown"


class _PagedQuery:
    """Fake PostgREST query honouring .range() so paging is exercised."""

    def __init__(self, rows):
        self.rows, self.bounds = rows, (0, len(rows) - 1)

    def range(self, start, end):
        self.bounds = (start, end)
        return self

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *a, **k: self

    def execute(self):
        start, end = self.bounds
        return type("R", (), {"data": self.rows[start : end + 1]})()


@pytest.fixture
def admin_client(monkeypatch):
    rows = [_log("success", BR, 800)] * 2300 + [_log("failed", BR)] * 200
    fake = type("Fake", (), {"table": lambda self, name: _PagedQuery(rows)})()
    monkeypatch.setattr(dashboard_api, "get_supabase", lambda: fake)
    monkeypatch.setattr(settings, "admin_dashboard_password", "pw")
    from app.main import app

    return TestClient(app)


def test_endpoint_pages_past_row_cap_and_requires_admin(admin_client):
    assert admin_client.get("/v1/dashboard/stats/sites").status_code == 401

    response = admin_client.get("/v1/dashboard/stats/sites?days=1", auth=("admin", "pw"))
    assert response.status_code == 200
    [br] = response.json()
    assert br["attempts"] == 2500  # all three pages, not just the first 1000
    assert br["success_rate"] == 92.0
