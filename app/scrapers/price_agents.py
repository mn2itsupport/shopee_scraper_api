"""The shopee_th price agents (scripts/price_agent.py) the API sends work to.

One agent per machine, each driving one logged-in Chrome tab at a time, so
the pool hands each agent one request at a time and sends a URL to whichever
agent is free. With a single agent configured this is exactly the old
behaviour: one queue, one request at a time.
"""

import asyncio
import time
from contextlib import asynccontextmanager

from app.config import settings


def agent_urls() -> list[str]:
    """shopee_th_price_agent_urls (comma-separated) when set, else the single
    shopee_th_price_agent_url, else none."""
    if settings.shopee_th_price_agent_urls.strip():
        urls = settings.shopee_th_price_agent_urls.split(",")
    else:
        urls = [settings.shopee_th_price_agent_url]
    return list(dict.fromkeys(u.strip().rstrip("/") for u in urls if u.strip()))


class AgentPool:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._condition: asyncio.Condition | None = None
        self._busy: set[str] = set()
        self._last_used: dict[str, float] = {}
        # Maintained by price_agent_monitor: agents failing their /status
        # checks only get work when every other agent is down too.
        self.down: set[str] = set()

    def _cond(self) -> asyncio.Condition:
        # Bound to the running loop (tests run each on a fresh one).
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self._loop, self._condition, self._busy = loop, asyncio.Condition(), set()
        return self._condition

    def _pick(self, exclude) -> str | None:
        candidates = [u for u in agent_urls() if u not in exclude]
        candidates = [u for u in candidates if u not in self.down] or candidates
        free = [u for u in candidates if u not in self._busy]
        # Idle longest first, so the work (and Shopee's view of each
        # account) is spread evenly.
        return min(free, key=lambda u: self._last_used.get(u, 0.0)) if free else None

    @asynccontextmanager
    async def acquire(self, exclude=()):
        """Yields an agent's base URL, waiting until one is free, or None
        when every agent is in `exclude`."""
        cond = self._cond()
        async with cond:
            while True:
                if not [u for u in agent_urls() if u not in exclude]:
                    url = None
                    break
                url = self._pick(exclude)
                if url is not None:
                    self._busy.add(url)
                    self._last_used[url] = time.monotonic()
                    break
                await cond.wait()
        try:
            yield url
        finally:
            if url is not None:
                async with cond:
                    self._busy.discard(url)
                    cond.notify_all()


pool = AgentPool()
