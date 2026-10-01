"""Verifies ShopeeBRScraper.fetch_pdp_via_apify's transport fallback chain
(apify -> dataset API -> unlocker API) without any network/vendor calls —
each transport is mocked, so this tests the *control flow* (which transport
runs, in what order, under what config) rather than any vendor's actual
behavior. No test here talks to Shopee or a live vendor API.
"""

import os
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("SUPABASE_URL", "http://example.invalid")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from app.config import settings  # noqa: E402
from app.models.schemas import PDPData  # noqa: E402
from app.scrapers.base import CaptchaBlockedError, ProductNotFoundError, ScraperError  # noqa: E402
from app.scrapers.sites.shopee_br import ShopeeBRScraper  # noqa: E402

URL = "https://shopee.com.br/product-slug-i.123.456"


def _pdp(source: str) -> PDPData:
    return PDPData(site_key="shopee_br", product_url=URL, title=f"from {source}")


@pytest.fixture
def scraper():
    return ShopeeBRScraper()


@pytest.fixture(autouse=True)
def _isolate_vendor_config(monkeypatch):
    # Every test starts with both secondary transports "unconfigured" unless
    # a test explicitly opts in — mirrors a fresh .env with no dataset id/
    # token set, so config-gating behavior is exercised by default rather
    # than by accident of whatever's in the real .env.
    monkeypatch.setattr(settings, "brightdata_shopee_dataset_id", "")
    monkeypatch.setattr(settings, "brightdata_api_token", "")


@pytest.mark.asyncio
async def test_apify_success_short_circuits(scraper, monkeypatch):
    monkeypatch.setattr(scraper, "_apify_fetch", AsyncMock(return_value=_pdp("apify")))
    monkeypatch.setattr(scraper, "_dataset_api_fetch", AsyncMock(side_effect=AssertionError("should not be called")))
    monkeypatch.setattr(scraper, "fetch_pdp_via_unlocker_api", AsyncMock(side_effect=AssertionError("should not be called")))

    result = await scraper.fetch_pdp_via_apify(URL)

    assert result.title == "from apify"


@pytest.mark.asyncio
async def test_falls_back_to_dataset_api_when_configured(scraper, monkeypatch):
    monkeypatch.setattr(settings, "brightdata_shopee_dataset_id", "gd_fake")
    monkeypatch.setattr(scraper, "_apify_fetch", AsyncMock(side_effect=ScraperError("apify down")))
    monkeypatch.setattr(scraper, "_dataset_api_fetch", AsyncMock(return_value=_pdp("dataset_api")))
    monkeypatch.setattr(scraper, "fetch_pdp_via_unlocker_api", AsyncMock(side_effect=AssertionError("should not be called")))

    result = await scraper.fetch_pdp_via_apify(URL)

    assert result.title == "from dataset_api"


@pytest.mark.asyncio
async def test_dataset_api_skipped_when_not_configured(scraper, monkeypatch):
    # dataset_id left empty by the autouse fixture.
    monkeypatch.setattr(scraper, "_apify_fetch", AsyncMock(side_effect=ScraperError("apify down")))
    dataset_mock = AsyncMock(side_effect=AssertionError("should not be called"))
    monkeypatch.setattr(scraper, "_dataset_api_fetch", dataset_mock)
    monkeypatch.setattr(settings, "brightdata_api_token", "tok_fake")
    monkeypatch.setattr(scraper, "fetch_pdp_via_unlocker_api", AsyncMock(return_value=_pdp("unlocker_api")))

    result = await scraper.fetch_pdp_via_apify(URL)

    dataset_mock.assert_not_called()
    assert result.title == "from unlocker_api"


@pytest.mark.asyncio
async def test_falls_back_to_unlocker_api_as_last_resort(scraper, monkeypatch):
    monkeypatch.setattr(settings, "brightdata_shopee_dataset_id", "gd_fake")
    monkeypatch.setattr(settings, "brightdata_api_token", "tok_fake")
    monkeypatch.setattr(scraper, "_apify_fetch", AsyncMock(side_effect=ScraperError("apify down")))
    monkeypatch.setattr(scraper, "_dataset_api_fetch", AsyncMock(side_effect=ScraperError("dataset down")))
    monkeypatch.setattr(scraper, "fetch_pdp_via_unlocker_api", AsyncMock(return_value=_pdp("unlocker_api")))

    result = await scraper.fetch_pdp_via_apify(URL)

    assert result.title == "from unlocker_api"


@pytest.mark.asyncio
async def test_reraises_original_error_when_no_fallback_configured(scraper, monkeypatch):
    # Both dataset_id and token left empty by the autouse fixture — nothing
    # to fall back to, so the apify failure itself should surface.
    monkeypatch.setattr(scraper, "_apify_fetch", AsyncMock(side_effect=ScraperError("apify down")))
    monkeypatch.setattr(scraper, "_dataset_api_fetch", AsyncMock(side_effect=AssertionError("should not be called")))
    monkeypatch.setattr(scraper, "fetch_pdp_via_unlocker_api", AsyncMock(side_effect=AssertionError("should not be called")))

    with pytest.raises(ScraperError, match="apify down"):
        await scraper.fetch_pdp_via_apify(URL)


@pytest.mark.asyncio
async def test_reraises_last_attempted_error_when_all_configured_transports_fail(scraper, monkeypatch):
    monkeypatch.setattr(settings, "brightdata_shopee_dataset_id", "gd_fake")
    monkeypatch.setattr(settings, "brightdata_api_token", "tok_fake")
    monkeypatch.setattr(scraper, "_apify_fetch", AsyncMock(side_effect=ScraperError("apify down")))
    monkeypatch.setattr(scraper, "_dataset_api_fetch", AsyncMock(side_effect=ScraperError("dataset down")))
    monkeypatch.setattr(scraper, "fetch_pdp_via_unlocker_api", AsyncMock(side_effect=ScraperError("unlocker down")))

    with pytest.raises(ScraperError, match="unlocker down"):
        await scraper.fetch_pdp_via_apify(URL)


@pytest.mark.asyncio
async def test_product_not_found_from_apify_still_falls_back(scraper, monkeypatch):
    # ProductNotFoundError is a ScraperError subclass — apify's own
    # "no records" signal is documented as unreliable (can't tell a dead
    # item from a cache miss), so this should fall through like any other
    # ScraperError rather than being treated as a final answer.
    monkeypatch.setattr(settings, "brightdata_shopee_dataset_id", "gd_fake")
    monkeypatch.setattr(scraper, "_apify_fetch", AsyncMock(side_effect=ProductNotFoundError("no records")))
    monkeypatch.setattr(scraper, "_dataset_api_fetch", AsyncMock(return_value=_pdp("dataset_api")))

    result = await scraper.fetch_pdp_via_apify(URL)

    assert result.title == "from dataset_api"


@pytest.mark.asyncio
async def test_captcha_blocked_from_unlocker_api_propagates_uncaught(scraper, monkeypatch):
    # CaptchaBlockedError is deliberately NOT a ScraperError subclass (see
    # base.py) — it must propagate past this method uncaught so
    # scrape_with_retries' own backoff-and-retry loop handles it, instead of
    # being swallowed here as a plain failure.
    monkeypatch.setattr(settings, "brightdata_shopee_dataset_id", "gd_fake")
    monkeypatch.setattr(settings, "brightdata_api_token", "tok_fake")
    monkeypatch.setattr(scraper, "_apify_fetch", AsyncMock(side_effect=ScraperError("apify down")))
    monkeypatch.setattr(scraper, "_dataset_api_fetch", AsyncMock(side_effect=ScraperError("dataset down")))
    monkeypatch.setattr(scraper, "fetch_pdp_via_unlocker_api", AsyncMock(side_effect=CaptchaBlockedError("walled")))

    with pytest.raises(CaptchaBlockedError):
        await scraper.fetch_pdp_via_apify(URL)
