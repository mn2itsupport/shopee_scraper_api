"""Watches each shopee_th price agent (scripts/price_agent.py, on the machine
with the real logged-in Chrome) and alerts when one goes down or recovers.

Since shopee_th asks the agent first, an agent outage means nearly every
shopee_th request falls back to Bright Data, which 502s on most product pages
(confirmed live 2026-10-04) — so an outage needs a human to look at the
laptop, Chrome, or the tunnel. Alerts only on state changes, after
settings.price_agent_alert_after_failures consecutive failed checks. A down
agent is also reported to price_agents.pool, which then sends work to the
other agents while any of them is up.
"""

import asyncio
import logging

from app.config import settings
from app.scrapers import http_pool, price_agents

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None


class _State:
    def __init__(self) -> None:
        self.failures = 0
        self.down = False


_states: dict[str, _State] = {}


async def check_agent(base: str) -> str | None:
    """Returns None if the agent and its Chrome are healthy, else a reason."""
    client = http_pool.get_client()
    try:
        response = await client.get(f"{base}/status", timeout=15)
        if response.status_code == 404:
            # Agent still running code from before /status existed.
            response = await client.get(f"{base}/health", timeout=15)
            response.raise_for_status()
            return None
        response.raise_for_status()
        body = response.json()
    except Exception as exc:
        return f"agent unreachable ({type(exc).__name__}: {exc})"[:300]
    if not body.get("chrome", True):
        return "agent is up but Chrome isn't answering on its CDP port"
    return None


def _webhook_url() -> str:
    return settings.price_agent_alert_webhook_url or settings.shopee_th_session_alert_webhook_url


async def _alert(message: str) -> None:
    logger.warning("price agent alert: %s", message)
    url = _webhook_url()
    if not url:
        return
    text = f"[shopee_scraper_api] {message}"
    try:
        # "text" for Slack-style webhooks, "content" for Discord.
        await http_pool.get_client().post(url, json={"text": text, "content": text}, timeout=15)
    except Exception:
        logger.warning("Failed to POST price agent alert to configured webhook", exc_info=True)


async def record(base: str, reason: str | None) -> None:
    state = _states.setdefault(base, _State())
    if reason is None:
        if state.down:
            price_agents.pool.down.discard(base)
            await _alert(f"shopee_th price agent {base} has recovered")
        state.failures = 0
        state.down = False
        return
    state.failures += 1
    logger.info("price agent %s check failed (%d in a row): %s", base, state.failures, reason)
    if not state.down and state.failures >= settings.price_agent_alert_after_failures:
        state.down = True
        price_agents.pool.down.add(base)
        others_up = [u for u in price_agents.agent_urls() if u != base and u not in price_agents.pool.down]
        impact = (
            f"Its work now goes to {len(others_up)} other agent(s)"
            if others_up
            else "All shopee_th requests now fall back to Bright Data and will mostly fail"
        )
        await _alert(
            f"shopee_th price agent {base} is DOWN: {reason}. {impact} — check that machine "
            "is on, Chrome is running and logged in, scripts/price_agent_watchdog.ps1 is running, "
            "and the Tailscale funnel is up."
        )


async def _check_and_record(base: str) -> None:
    try:
        await record(base, await check_agent(base))
    except Exception:
        logger.warning("price agent %s check itself failed", base, exc_info=True)


async def _run() -> None:
    while True:
        await asyncio.gather(*(_check_and_record(base) for base in price_agents.agent_urls()))
        await asyncio.sleep(settings.price_agent_check_interval_seconds)


def start() -> None:
    global _task
    if price_agents.agent_urls() and _task is None:
        _task = asyncio.create_task(_run())


async def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
