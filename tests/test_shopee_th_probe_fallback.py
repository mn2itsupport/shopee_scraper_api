"""Verifies ShopeeTHScraper.fetch_pdp_via_curl_cffi falls back to the price
probe's pdp/get_pc body when curl_cffi fails — curl_cffi and the probe are
both mocked, so this tests the control flow only. No test here talks to
Shopee, Bright Data, or a real price agent.
"""

import asyncio
import os
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("SUPABASE_URL", "http://example.invalid")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from app.config import settings  # noqa: E402
from app.models.schemas import PDPData  # noqa: E402
from app.scrapers.base import ProductNotFoundError, ScraperError  # noqa: E402
from app.scrapers.sites import shopee_th  # noqa: E402
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
    # The tests below cover the parallel curl_cffi + probe path; agent-first
    # has its own tests at the end of this file.
    monkeypatch.setattr(settings, "shopee_th_price_agent_first", False)


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


def _slow_probe(body, delay, events):
    async def probe():
        try:
            await asyncio.sleep(delay)
            events.append("probe finished")
            return body
        except asyncio.CancelledError:
            events.append("probe cancelled")
            raise

    return lambda url: probe()


@pytest.mark.asyncio
async def test_curl_failure_waits_for_queued_probe(scraper, monkeypatch):
    events = []
    monkeypatch.setattr(scraper, "_curl_cffi_fetch", AsyncMock(side_effect=ScraperError("HTTP 502")))
    monkeypatch.setattr(scraper, "_select_price_probe", _slow_probe(GET_PC_BODY, 0.2, events))

    result = await scraper.fetch_pdp_via_curl_cffi(URL)

    assert result.title == "probe title"
    assert events == ["probe finished"]


@pytest.mark.asyncio
async def test_curl_success_skips_queued_probe_in_shadow_mode(scraper, monkeypatch):
    events = []
    curl_pdp = PDPData(site_key="shopee_th", product_url=URL, title="curl title", raw={"data": {"item": {}}})

    async def curl(url):
        await asyncio.sleep(0.05)  # long enough for the probe to start waiting
        return curl_pdp

    monkeypatch.setattr(scraper, "_curl_cffi_fetch", curl)
    monkeypatch.setattr(scraper, "_select_price_probe", _slow_probe(GET_PC_BODY, 5, events))

    result = await asyncio.wait_for(scraper.fetch_pdp_via_curl_cffi(URL), timeout=1)
    await asyncio.sleep(0)

    assert result.title == "curl title"
    assert events == ["probe cancelled"]


@pytest.mark.asyncio
async def test_curl_success_waits_for_probe_when_merge_on(scraper, monkeypatch):
    monkeypatch.setattr(settings, "shopee_th_price_probe_merge", True)
    events = []
    curl_pdp = PDPData(site_key="shopee_th", product_url=URL, title="curl title", raw={"data": {"item": {}}})
    monkeypatch.setattr(scraper, "_curl_cffi_fetch", AsyncMock(return_value=curl_pdp))
    monkeypatch.setattr(scraper, "_select_price_probe", _slow_probe(GET_PC_BODY, 0.2, events))

    result = await scraper.fetch_pdp_via_curl_cffi(URL)

    assert result.price == 4.0
    assert events == ["probe finished"]


@pytest.mark.asyncio
async def test_request_timeout_cancels_queued_probe(scraper, monkeypatch):
    events = []

    async def slow_curl(url):
        await asyncio.sleep(5)

    monkeypatch.setattr(scraper, "_curl_cffi_fetch", slow_curl)
    monkeypatch.setattr(scraper, "_select_price_probe", _slow_probe(GET_PC_BODY, 5, events))

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(scraper.fetch_pdp_via_curl_cffi(URL), timeout=0.2)
    await asyncio.sleep(0)

    assert events == ["probe cancelled"]


@pytest.mark.asyncio
async def test_agent_calls_run_one_at_a_time(scraper, monkeypatch):
    monkeypatch.setattr(settings, "shopee_th_price_agent_url", "https://agent.invalid")
    state = {"active": 0, "max_active": 0}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return GET_PC_BODY

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *args, **kwargs):
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
            await asyncio.sleep(0.05)
            state["active"] -= 1
            return FakeResponse()

    monkeypatch.setattr(shopee_th.httpx, "AsyncClient", FakeClient)

    bodies = await asyncio.gather(*(scraper._fetch_get_pc_via_agent(URL) for _ in range(5)))

    assert bodies == [GET_PC_BODY] * 5
    assert state["max_active"] == 1


@pytest.mark.asyncio
async def test_merge_on_returns_without_price_when_probe_too_slow(scraper, monkeypatch):
    monkeypatch.setattr(settings, "shopee_th_price_probe_merge", True)
    monkeypatch.setattr(settings, "shopee_th_price_probe_merge_wait_seconds", 0.1)
    events = []
    curl_pdp = PDPData(site_key="shopee_th", product_url=URL, title="curl title", raw={"data": {"item": {}}})
    monkeypatch.setattr(scraper, "_curl_cffi_fetch", AsyncMock(return_value=curl_pdp))
    monkeypatch.setattr(scraper, "_select_price_probe", _slow_probe(GET_PC_BODY, 5, events))

    result = await asyncio.wait_for(scraper.fetch_pdp_via_curl_cffi(URL), timeout=1)
    await asyncio.sleep(0)

    assert result.title == "curl title"
    assert result.price is None
    assert events == ["probe cancelled"]


@pytest.fixture
def _agent_first(monkeypatch):
    monkeypatch.setattr(settings, "shopee_th_price_agent_first", True)
    monkeypatch.setattr(settings, "shopee_th_price_agent_url", "https://agent.invalid")


@pytest.mark.asyncio
async def test_agent_first_answers_without_calling_curl(scraper, monkeypatch, _agent_first):
    curl = AsyncMock(side_effect=AssertionError("curl_cffi should not be called"))
    monkeypatch.setattr(scraper, "_curl_cffi_fetch", curl)
    monkeypatch.setattr(scraper, "_select_price_probe", lambda url: AsyncMock(return_value=GET_PC_BODY)())

    result = await scraper.fetch_pdp_via_curl_cffi(URL)

    assert result.title == "probe title"
    assert result.price == 4.0
    assert result.raw == GET_PC_BODY
    curl.assert_not_called()


@pytest.mark.asyncio
async def test_agent_first_not_found_skips_curl(scraper, monkeypatch, _agent_first):
    not_found = {"bff_meta": None, "error": 266900002, "error_msg": None, "data": None}
    curl = AsyncMock(side_effect=AssertionError("curl_cffi should not be called"))
    monkeypatch.setattr(scraper, "_curl_cffi_fetch", curl)
    monkeypatch.setattr(scraper, "_select_price_probe", lambda url: AsyncMock(return_value=not_found)())

    with pytest.raises(ProductNotFoundError):
        await scraper.fetch_pdp_via_curl_cffi(URL)
    curl.assert_not_called()


@pytest.mark.asyncio
async def test_agent_first_falls_back_to_curl_once(scraper, monkeypatch, _agent_first):
    curl_pdp = PDPData(site_key="shopee_th", product_url=URL, title="curl title", raw={"data": {"item": {}}})
    probe = AsyncMock(return_value=None)
    monkeypatch.setattr(scraper, "_curl_cffi_fetch", AsyncMock(return_value=curl_pdp))
    monkeypatch.setattr(scraper, "_select_price_probe", lambda url: probe())

    result = await scraper.fetch_pdp_via_curl_cffi(URL)

    assert result.title == "curl title"
    assert probe.await_count == 1


@pytest.mark.asyncio
async def test_agent_first_off_without_agent_configured(scraper, monkeypatch, _agent_first):
    monkeypatch.setattr(settings, "shopee_th_price_agent_url", "")
    monkeypatch.setattr(settings, "shopee_th_real_chrome_cdp_url", "")
    curl_pdp = PDPData(site_key="shopee_th", product_url=URL, title="curl title", raw={"data": {"item": {}}})
    _mock(scraper, monkeypatch, curl={"return_value": curl_pdp}, probe={"return_value": GET_PC_BODY})

    result = await scraper.fetch_pdp_via_curl_cffi(URL)

    assert result.title == "curl title"
