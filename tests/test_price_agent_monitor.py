"""Verifies the price-agent monitor alerts once when the agent goes down
(after N failed checks) and once when it recovers. HTTP is mocked — no test
here talks to a real agent or webhook.
"""

import os

import httpx
import pytest

os.environ.setdefault("SUPABASE_URL", "http://example.invalid")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from app.config import settings  # noqa: E402
from app.scrapers import price_agent_monitor, price_agents  # noqa: E402

AGENT = "https://agent.invalid"
WEBHOOK = "https://hooks.invalid/alert"


@pytest.fixture
def agent(monkeypatch):
    """Fake agent + webhook behind http_pool; returns the mutable state."""
    state = {"status": 200, "body": {"ok": True, "chrome": True}, "alerts": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == WEBHOOK:
            state["alerts"].append(request.read().decode())
            return httpx.Response(200)
        if state["status"] is None:
            raise httpx.ConnectError("unreachable")
        return httpx.Response(state["status"], json=state["body"])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(price_agent_monitor.http_pool, "get_client", lambda: client)
    monkeypatch.setattr(settings, "shopee_th_price_agent_url", AGENT)
    monkeypatch.setattr(settings, "price_agent_alert_webhook_url", WEBHOOK)
    monkeypatch.setattr(settings, "price_agent_alert_after_failures", 3)
    monkeypatch.setattr(price_agent_monitor, "_states", {})
    monkeypatch.setattr(price_agents, "pool", price_agents.AgentPool())
    return state


async def _checks(n):
    for _ in range(n):
        await price_agent_monitor.record(AGENT, await price_agent_monitor.check_agent(AGENT))


@pytest.mark.asyncio
async def test_healthy_agent_never_alerts(agent):
    await _checks(5)
    assert agent["alerts"] == []


@pytest.mark.asyncio
async def test_alerts_once_after_threshold_then_on_recovery(agent):
    agent["status"] = None
    await _checks(2)
    assert agent["alerts"] == []

    await _checks(3)
    assert len(agent["alerts"]) == 1
    assert "DOWN" in agent["alerts"][0]

    agent["status"] = 200
    await _checks(2)
    assert len(agent["alerts"]) == 2
    assert "recovered" in agent["alerts"][1]


@pytest.mark.asyncio
async def test_chrome_down_counts_as_failure(agent):
    agent["body"] = {"ok": False, "chrome": False}
    await _checks(3)
    assert len(agent["alerts"]) == 1
    assert "Chrome" in agent["alerts"][0]


@pytest.mark.asyncio
async def test_one_success_resets_the_count(agent):
    agent["status"] = 502
    await _checks(2)
    agent["status"] = 200
    await _checks(1)
    agent["status"] = 502
    await _checks(2)
    assert agent["alerts"] == []


@pytest.mark.asyncio
async def test_old_agent_without_status_falls_back_to_health(agent, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404 if request.url.path == "/status" else 200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(price_agent_monitor.http_pool, "get_client", lambda: client)

    assert await price_agent_monitor.check_agent(AGENT) is None


@pytest.mark.asyncio
async def test_down_agent_is_reported_to_the_pool_and_named(agent):
    agent["status"] = None
    await _checks(3)
    assert price_agents.pool.down == {AGENT}
    assert AGENT in agent["alerts"][0]
    assert "Bright Data" in agent["alerts"][0]

    agent["status"] = 200
    await _checks(1)
    assert price_agents.pool.down == set()


@pytest.mark.asyncio
async def test_agents_are_tracked_separately(agent, monkeypatch):
    other = "https://agent2.invalid"
    monkeypatch.setattr(settings, "shopee_th_price_agent_urls", f"{AGENT},{other}")
    for _ in range(3):
        await price_agent_monitor.record(other, "unreachable")
        await price_agent_monitor.record(AGENT, None)

    assert price_agents.pool.down == {other}
    assert len(agent["alerts"]) == 1
    assert other in agent["alerts"][0]
    assert "1 other agent" in agent["alerts"][0]
