"""One shared, connection-pooled httpx.AsyncClient for the process, used by
every fetch_pdp_via_unlocker_api call. A fresh client per call (the previous
approach) re-does a TCP+TLS handshake to Bright Data on every single request —
fine at low volume, but the dominant cost once you're pushing thousands of
requests/hour. Reusing one pooled client lets httpx keep warm keep-alive
connections to api.brightdata.com and reuse them across concurrent scrapes.
"""

import httpx

from app.config import settings

_client: httpx.AsyncClient | None = None


def startup() -> None:
    global _client
    _client = httpx.AsyncClient(
        timeout=settings.scrape_timeout_seconds,
        limits=httpx.Limits(
            max_connections=settings.http_pool_max_connections,
            max_keepalive_connections=settings.http_pool_max_keepalive,
        ),
    )


async def shutdown() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def get_client() -> httpx.AsyncClient:
    assert _client is not None, "http_pool.startup() not called"
    return _client
