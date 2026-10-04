"""Verifies the shopee_th price agent pool: one request per agent at a time,
work spread across agents, failover to the next agent, and that a single
configured agent behaves exactly as before. Agents are faked at
ShopeeTHScraper._ask_agent — nothing here talks to a real agent.
"""

import asyncio
import os

import pytest

os.environ.setdefault("SUPABASE_URL", "http://example.invalid")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from app.config import settings  # noqa: E402
from app.scrapers import price_agents  # noqa: E402
from app.scrapers.sites.shopee_th import ShopeeTHScraper  # noqa: E402

URL = "https://shopee.co.th/product-i.481607585.11168850119"
A, B = "https://agent-a.invalid", "https://agent-b.invalid"
OK = {"error": None, "data": {"item": {"price": 400000}}}
FLAGGED = {"error": 90309999, "data": None}
NOT_FOUND = {"error": 266900002, "data": None}


@pytest.fixture(autouse=True)
def _fresh_pool(monkeypatch):
    monkeypatch.setattr(price_agents, "pool", price_agents.AgentPool())
    monkeypatch.setattr(settings, "shopee_th_price_agent_url", "")
    monkeypatch.setattr(settings, "shopee_th_price_agent_urls", "")


def _fake_agents(monkeypatch, scraper, answers=None, delay=0.02):
    """Each agent answers OK after `delay`, unless `answers` maps it to
    something else (a body, None for a failure). Returns call stats."""
    stats = {"calls": [], "active": {}, "max_active": {}, "max_total": 0}

    async def ask(agent, url):
        stats["calls"].append(agent)
        stats["active"][agent] = stats["active"].get(agent, 0) + 1
        stats["max_active"][agent] = max(stats["max_active"].get(agent, 0), stats["active"][agent])
        stats["max_total"] = max(stats["max_total"], sum(stats["active"].values()))
        await asyncio.sleep(delay)
        stats["active"][agent] -= 1
        return (answers or {}).get(agent, OK)

    monkeypatch.setattr(scraper, "_ask_agent", ask)
    return stats


def test_agent_urls_parsing(monkeypatch):
    monkeypatch.setattr(settings, "shopee_th_price_agent_url", A + "/")
    assert price_agents.agent_urls() == [A]

    monkeypatch.setattr(settings, "shopee_th_price_agent_urls", f" {A}/ , {B},,{A}")
    assert price_agents.agent_urls() == [A, B]

    monkeypatch.setattr(settings, "shopee_th_price_agent_url", "")
    monkeypatch.setattr(settings, "shopee_th_price_agent_urls", "")
    assert price_agents.agent_urls() == []


@pytest.mark.asyncio
async def test_single_agent_still_one_at_a_time(monkeypatch):
    monkeypatch.setattr(settings, "shopee_th_price_agent_url", A)
    scraper = ShopeeTHScraper()
    stats = _fake_agents(monkeypatch, scraper)

    bodies = await asyncio.gather(*(scraper._fetch_get_pc_via_agent(URL) for _ in range(4)))

    assert bodies == [OK] * 4
    assert stats["max_total"] == 1
    assert stats["calls"] == [A] * 4


@pytest.mark.asyncio
async def test_two_agents_share_the_work_one_request_each(monkeypatch):
    monkeypatch.setattr(settings, "shopee_th_price_agent_urls", f"{A},{B}")
    scraper = ShopeeTHScraper()
    stats = _fake_agents(monkeypatch, scraper)

    bodies = await asyncio.gather(*(scraper._fetch_get_pc_via_agent(URL) for _ in range(6)))

    assert bodies == [OK] * 6
    assert stats["max_active"] == {A: 1, B: 1}
    assert stats["max_total"] == 2
    assert stats["calls"].count(A) == 3 and stats["calls"].count(B) == 3


@pytest.mark.asyncio
async def test_failed_agent_hands_url_to_the_other(monkeypatch):
    monkeypatch.setattr(settings, "shopee_th_price_agent_urls", f"{A},{B}")
    scraper = ShopeeTHScraper()
    stats = _fake_agents(monkeypatch, scraper, answers={A: None})

    assert await scraper._fetch_get_pc_via_agent(URL) == OK
    assert stats["calls"] == [A, B]


@pytest.mark.asyncio
async def test_flagged_account_hands_url_to_the_other(monkeypatch):
    monkeypatch.setattr(settings, "shopee_th_price_agent_urls", f"{A},{B}")
    scraper = ShopeeTHScraper()
    stats = _fake_agents(monkeypatch, scraper, answers={A: FLAGGED})

    assert await scraper._fetch_get_pc_via_agent(URL) == OK
    assert stats["calls"] == [A, B]


@pytest.mark.asyncio
async def test_not_found_is_final(monkeypatch):
    monkeypatch.setattr(settings, "shopee_th_price_agent_urls", f"{A},{B}")
    scraper = ShopeeTHScraper()
    stats = _fake_agents(monkeypatch, scraper, answers={A: NOT_FOUND})

    assert await scraper._fetch_get_pc_via_agent(URL) == NOT_FOUND
    assert stats["calls"] == [A]


@pytest.mark.asyncio
async def test_all_agents_failing_returns_last_answer(monkeypatch):
    monkeypatch.setattr(settings, "shopee_th_price_agent_urls", f"{A},{B}")
    scraper = ShopeeTHScraper()
    stats = _fake_agents(monkeypatch, scraper, answers={A: None, B: FLAGGED})

    assert await scraper._fetch_get_pc_via_agent(URL) == FLAGGED
    assert stats["calls"] == [A, B]


@pytest.mark.asyncio
async def test_down_agent_skipped_while_another_is_up(monkeypatch):
    monkeypatch.setattr(settings, "shopee_th_price_agent_urls", f"{A},{B}")
    price_agents.pool.down.add(A)
    scraper = ShopeeTHScraper()
    stats = _fake_agents(monkeypatch, scraper)

    await asyncio.gather(*(scraper._fetch_get_pc_via_agent(URL) for _ in range(3)))
    assert stats["calls"] == [B] * 3

    price_agents.pool.down.add(B)
    await scraper._fetch_get_pc_via_agent(URL)
    assert len(stats["calls"]) == 4


@pytest.mark.asyncio
async def test_cancelled_request_frees_its_agent(monkeypatch):
    monkeypatch.setattr(settings, "shopee_th_price_agent_url", A)
    scraper = ShopeeTHScraper()
    _fake_agents(monkeypatch, scraper, delay=5)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(scraper._fetch_get_pc_via_agent(URL), timeout=0.05)

    _fake_agents(monkeypatch, scraper, delay=0)
    assert await asyncio.wait_for(scraper._fetch_get_pc_via_agent(URL), timeout=1) == OK
