import logging
from functools import lru_cache

import httpx
from postgrest._sync.client import SyncPostgrestClient
from postgrest.utils import SyncClient
from supabase import Client, create_client

from app.config import settings

logger = logging.getLogger(__name__)

# Safe to resend: reads, plus writes keyed so a repeat lands the same way.
_IDEMPOTENT_METHODS = {"GET", "HEAD", "OPTIONS", "PUT", "DELETE"}


class _RetryingTransport(httpx.HTTPTransport):
    """Retries a request once on a dropped connection.

    Supabase closes idle keep-alive connections; the next request reusing one
    fails with "Server disconnected" (httpx.RemoteProtocolError) before the
    server ever sees it — confirmed live 2026-10-04 as 500s from auth and
    check_quota when a burst arrived after an idle spell. A connect error is
    always safe to retry (nothing was sent); a disconnect only for
    idempotent methods, so a usage_logs insert is never written twice.
    """

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return super().handle_request(request)
        except httpx.ConnectError:
            pass
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError):
            if request.method not in _IDEMPOTENT_METHODS:
                raise
        logger.info("Supabase connection dropped; retrying %s %s once", request.method, request.url.path)
        return super().handle_request(request)


def _create_session(self, base_url, headers, timeout, verify=True) -> SyncClient:
    # postgrest's own default, except HTTP/1.1 instead of HTTP/2 — one
    # multiplexed HTTP/2 connection shared by every asyncio.to_thread worker
    # takes all in-flight requests down with it when Supabase drops it —
    # and the retrying transport above.
    return SyncClient(
        base_url=base_url,
        headers=headers,
        timeout=timeout,
        follow_redirects=True,
        transport=_RetryingTransport(verify=verify),
    )


# Patched on the class, not one instance: supabase rebuilds its postgrest
# client (and so its session) on auth events and .schema() calls.
SyncPostgrestClient.create_session = _create_session


@lru_cache
def get_supabase() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_role_key)
