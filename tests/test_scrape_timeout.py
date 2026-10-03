"""The overall scrape cap: one request (all retries together) must end with a
clean ScrapeTimeoutError / HTTP 504 instead of outliving the gateway timeout.
No network or Supabase calls — the scraper and DB helpers are stubbed.
"""

import asyncio
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SUPABASE_URL", "http://example.invalid")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from app.config import settings  # noqa: E402
from app.core.auth import require_api_key  # noqa: E402
from app.models.schemas import AuthedKey  # noqa: E402
from app.scrapers import registry  # noqa: E402
from app.scrapers.base import ScrapeTimeoutError  # noqa: E402


def test_scrape_with_retries_is_capped(monkeypatch):
    async def never_finishes(site_key, url):
        await asyncio.sleep(5)

    monkeypatch.setattr(registry, "_scrape_attempts", never_finishes)
    monkeypatch.setattr(settings, "scrape_total_timeout_seconds", 0.05)

    with pytest.raises(ScrapeTimeoutError) as excinfo:
        asyncio.run(registry.scrape_with_retries("shopee_br", "https://shopee.com.br/x-i.1.2"))
    assert "timed out" in str(excinfo.value)


def test_endpoint_returns_504_on_timeout_and_logs_failed(monkeypatch):
    from app.main import app
    from app.routers import scrape as scrape_router

    logged = []

    async def slow(site_key, url):
        raise ScrapeTimeoutError("Scrape timed out after 240s (retries included). The target is responding slowly; try again.")

    monkeypatch.setattr(scrape_router, "scrape_with_retries", slow)
    monkeypatch.setattr(scrape_router, "get_site_id", lambda site_key: "site-1")
    monkeypatch.setattr(scrape_router, "check_burst_limit", lambda *a, **k: None)
    monkeypatch.setattr(scrape_router, "check_quota", lambda *a, **k: None)
    monkeypatch.setattr(scrape_router, "log_usage", lambda *args: logged.append(args) or "log-1")

    app.dependency_overrides[require_api_key] = lambda: AuthedKey(
        api_key_id="key-1", client_id="c-1", plan_id="p-1", requests_per_minute=30, daily_quota=1000, monthly_quota=20000
    )
    try:
        response = TestClient(app).post("/v1/shopee_br/pdp", json={"url": "https://shopee.com.br/x-i.1.2"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 504
    assert "timed out" in response.json()["detail"]
    # usage_logs.status only allows a fixed set of values, so a timeout is stored as "failed".
    assert logged and logged[0][3] == "failed"
