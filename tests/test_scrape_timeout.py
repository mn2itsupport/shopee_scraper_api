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


class _OneShotScraper:
    """Counts fetch attempts; every attempt raises the given error."""

    browser_mode_override = "curl_cffi"

    def __init__(self, error):
        self.error = error
        self.calls = 0

    async def fetch_pdp_via_curl_cffi(self, url):
        self.calls += 1
        raise self.error


def _run_attempts(monkeypatch, error):
    scraper = _OneShotScraper(error)
    monkeypatch.setattr(registry, "get_scraper", lambda site_key: scraper)
    monkeypatch.setattr(settings, "pre_scrape_jitter_ms_max", 0)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(registry.asyncio, "sleep", lambda *_: real_sleep(0))
    with pytest.raises(type(error)):
        asyncio.run(registry._scrape_attempts("shopee_th", "https://shopee.co.th/x-i.1.2"))
    return scraper.calls


def test_scraper_error_retried_once_by_default(monkeypatch):
    from app.scrapers.base import ScraperError

    assert _run_attempts(monkeypatch, ScraperError("HTTP 502")) == 2


def test_non_retryable_scraper_error_not_retried(monkeypatch):
    from app.scrapers.base import ScraperError

    error = ScraperError("HTTP 502")
    error.retryable = False
    assert _run_attempts(monkeypatch, error) == 1


def test_non_retryable_captcha_not_retried(monkeypatch):
    from app.scrapers.base import CaptchaBlockedError

    error = CaptchaBlockedError("wall")
    error.retryable = False
    assert _run_attempts(monkeypatch, error) == 1
