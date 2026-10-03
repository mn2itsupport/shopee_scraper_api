"""Verifies ShopeeTHScraper.fetch_pdp_via_curl_cffi falls back to the price
probe's pdp/get_pc body when curl_cffi fails — curl_cffi and the probe are
both mocked, so this tests the control flow only. No test here talks to
Shopee, Bright Data, or a real price agent.
"""

import os
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("SUPABASE_URL", "http://example.invalid")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from app.config import settings  # noqa: E402
from app.models.schemas import PDPData  # noqa: E402
from app.scrapers.base import ScraperError  # noqa: E402
from app.scrapers.sites.shopee_th import ShopeeTHScraper  # noqa: E402

URL = "https://shopee.co.th/product-i.481607585.11168850119"

GET_PC_BODY = {
    "bff_meta": None,
    "error": None,
    "error_msg": None,
    "data": {
        "item": {
            "name": "probe title",
            "price": 400000,
            "price_min": 400000,
            "price_max": 500000,
            "currency": "THB",
            "images": ["abc123"],
            "item_rating": {"rating_star": 4.8},
            "historical_sold": 12,
        }
    },
}


@pytest.fixture
def scraper():
    return ShopeeTHScraper()


@pytest.fixture(autouse=True)
def _probe_config(monkeypatch):
    monkeypatch.setattr(settings, "shopee_th_price_probe_enabled", True)
    monkeypatch.setattr(settings, "shopee_th_price_probe_merge", False)
    monkeypatch.setattr(settings, "shopee_th_price_probe_fallback", True)


def _mock(scraper, monkeypatch, *, curl, probe):
    monkeypatch.setattr(scraper, "_curl_cffi_fetch", AsyncMock(**curl))
    monkeypatch.setattr(scraper, "_select_price_probe", lambda url: AsyncMock(**probe)())


@pytest.mark.asyncio
async def test_curl_failure_answers_from_probe_body(scraper, monkeypatch):
    _mock(
        scraper,
        monkeypatch,
        curl={"side_effect": ScraperError("curl_cffi request failed: HTTP 502")},
        probe={"return_value": GET_PC_BODY},
    )

    result = await scraper.fetch_pdp_via_curl_cffi(URL)

    assert result.title == "probe title"
    assert result.price == 4.0
    assert result.rating == 4.8
    assert result.sold_count == 12
    assert result.external_product_id == "481607585.11168850119"
    assert result.raw == GET_PC_BODY


@pytest.mark.asyncio
async def test_fallback_reads_newer_get_pc_shape(scraper, monkeypatch):
    # Live shopee_th shape (2026-10): title instead of name, gallery and sold
    # counts moved off item.
    body = {
        "bff_meta": None,
        "error": None,
        "error_msg": None,
        "data": {
            "item": {"title": "new title", "price": 300000, "currency": "THB", "item_rating": {"rating_star": 4.6}},
            "product_images": {"images": ["img1", "img2"]},
            "product_review": {"historical_sold": 345},
        },
    }
    _mock(
        scraper,
        monkeypatch,
        curl={"side_effect": ScraperError("curl_cffi request failed: HTTP 502")},
        probe={"return_value": body},
    )

    result = await scraper.fetch_pdp_via_curl_cffi(URL)

    assert result.title == "new title"
    assert result.price == 3.0
    assert result.sold_count == 345
    assert result.image_urls == ["https://cf.shopee.co.th/file/img1", "https://cf.shopee.co.th/file/img2"]


@pytest.mark.asyncio
async def test_curl_failure_reraises_without_probe_item(scraper, monkeypatch):
    not_found = {"bff_meta": None, "error": 266900002, "error_msg": None, "data": None}
    _mock(
        scraper,
        monkeypatch,
        curl={"side_effect": ScraperError("curl_cffi request failed: HTTP 502")},
        probe={"return_value": not_found},
    )

    with pytest.raises(ScraperError, match="HTTP 502"):
        await scraper.fetch_pdp_via_curl_cffi(URL)


@pytest.mark.asyncio
async def test_curl_failure_reraises_when_probe_returns_none(scraper, monkeypatch):
    _mock(
        scraper,
        monkeypatch,
        curl={"side_effect": ScraperError("curl_cffi request failed: HTTP 502")},
        probe={"return_value": None},
    )

    with pytest.raises(ScraperError, match="HTTP 502"):
        await scraper.fetch_pdp_via_curl_cffi(URL)


@pytest.mark.asyncio
async def test_fallback_disabled_reraises(scraper, monkeypatch):
    monkeypatch.setattr(settings, "shopee_th_price_probe_fallback", False)
    _mock(
        scraper,
        monkeypatch,
        curl={"side_effect": ScraperError("curl_cffi request failed: HTTP 502")},
        probe={"return_value": GET_PC_BODY},
    )

    with pytest.raises(ScraperError, match="HTTP 502"):
        await scraper.fetch_pdp_via_curl_cffi(URL)


@pytest.mark.asyncio
async def test_curl_success_unchanged_in_shadow_mode(scraper, monkeypatch):
    curl_pdp = PDPData(site_key="shopee_th", product_url=URL, title="curl title", raw={"data": {"item": {}}})
    _mock(scraper, monkeypatch, curl={"return_value": curl_pdp}, probe={"return_value": GET_PC_BODY})

    result = await scraper.fetch_pdp_via_curl_cffi(URL)

    assert result.title == "curl title"
    assert result.price is None


@pytest.mark.asyncio
async def test_curl_success_merges_probe_price_when_merge_on(scraper, monkeypatch):
    monkeypatch.setattr(settings, "shopee_th_price_probe_merge", True)
    curl_pdp = PDPData(site_key="shopee_th", product_url=URL, title="curl title", raw={"data": {"item": {}}})
    _mock(scraper, monkeypatch, curl={"return_value": curl_pdp}, probe={"return_value": GET_PC_BODY})

    result = await scraper.fetch_pdp_via_curl_cffi(URL)

    assert result.title == "curl title"
    assert result.price == 4.0
    assert result.rating == 4.8
