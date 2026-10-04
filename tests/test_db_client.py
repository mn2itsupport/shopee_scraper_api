"""Verifies the Supabase session retries a dropped connection once, and only
resends a non-idempotent request when nothing was sent. The underlying
transport is mocked — no test here talks to Supabase.
"""

import os

import httpx
import pytest

os.environ.setdefault("SUPABASE_URL", "http://example.invalid")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from postgrest._sync.client import SyncPostgrestClient  # noqa: E402

from app.db import client as db_client  # noqa: E402


def _fail_then_succeed(monkeypatch, error):
    calls = []

    def handle(self, request):
        calls.append(request.method)
        if len(calls) == 1:
            raise error
        return httpx.Response(200, json=[])

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle)
    return calls


def _send(method):
    with httpx.Client(transport=db_client._RetryingTransport()) as session:
        return session.request(method, "https://db.invalid/rest/v1/api_keys")


def test_get_retried_after_server_disconnect(monkeypatch):
    calls = _fail_then_succeed(monkeypatch, httpx.RemoteProtocolError("Server disconnected"))

    assert _send("GET").status_code == 200
    assert calls == ["GET", "GET"]


def test_post_not_resent_after_server_disconnect(monkeypatch):
    calls = _fail_then_succeed(monkeypatch, httpx.RemoteProtocolError("Server disconnected"))

    with pytest.raises(httpx.RemoteProtocolError):
        _send("POST")
    assert calls == ["POST"]


def test_post_retried_after_connect_error(monkeypatch):
    calls = _fail_then_succeed(monkeypatch, httpx.ConnectError("refused"))

    assert _send("POST").status_code == 200
    assert calls == ["POST", "POST"]


def test_only_one_retry(monkeypatch):
    def handle(self, request):
        raise httpx.RemoteProtocolError("Server disconnected")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle)

    with pytest.raises(httpx.RemoteProtocolError):
        _send("GET")


def test_postgrest_sessions_use_retrying_http1_transport():
    pg = SyncPostgrestClient("https://db.invalid/rest/v1")

    assert isinstance(pg.session._transport, db_client._RetryingTransport)
    pg.session.close()
