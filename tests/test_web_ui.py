"""Smoke tests for the customer web UI (landing, login, /app pages, key
endpoints). Supabase is replaced by an in-memory fake, so these check routing,
auth/cookie handling and template rendering — not real database behavior.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SUPABASE_URL", "http://example.invalid")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from app.core.security import hash_api_key  # noqa: E402

RAW_KEY = "sk_test_web_ui_key_0001"
NOW = datetime.now(timezone.utc)

PLAN = {
    "name": "starter",
    "requests_per_minute": 30,
    "daily_quota": 1000,
    "monthly_quota": 20000,
    "duration_days": 30,
    "price_cents": 4900,
}


class _Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Query:
    def __init__(self, db, table):
        self.db, self.table_name = db, table
        self.filters, self._op, self._payload = {}, "select", None

    def select(self, *_args, **_kwargs):
        return self

    def insert(self, payload):
        self._op, self._payload = "insert", payload
        return self

    def update(self, payload):
        self._op, self._payload = "update", payload
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def __getattr__(self, name):  # gte/order/limit/... are no-ops
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *a, **k: self

    def execute(self):
        rows = [r for r in self.db.tables.get(self.table_name, []) if all(r.get(k) == v for k, v in self.filters.items())]
        if self._op == "insert":
            row = {"id": f"new-{len(self.db.tables.setdefault(self.table_name, []))}", **self._payload}
            self.db.tables[self.table_name].append(row)
            return _Result([row])
        if self._op == "update":
            for row in rows:
                row.update(self._payload)
            return _Result(rows)
        return _Result(rows, count=len(rows))


class FakeSupabase:
    def __init__(self):
        self.tables = {
            "api_keys": [
                {
                    "id": "key-1",
                    "client_id": "client-1",
                    "plan_id": "plan-1",
                    "key_hash": hash_api_key(RAW_KEY),
                    "key_prefix": RAW_KEY[:11],
                    "status": "active",
                    "created_at": NOW.isoformat(),
                    "expires_at": (NOW + timedelta(days=20)).isoformat(),
                    "clients": {"name": "Acme Corp", "email": "acme@example.com"},
                    "plans": PLAN,
                },
                {
                    "id": "key-2",
                    "client_id": "client-1",
                    "plan_id": "plan-1",
                    "key_hash": "other",
                    "key_prefix": "sk_other_aa",
                    "status": "active",
                    "created_at": NOW.isoformat(),
                    "expires_at": (NOW + timedelta(days=20)).isoformat(),
                    "clients": {"name": "Acme Corp", "email": "acme@example.com"},
                    "plans": PLAN,
                },
            ],
            "plans": [{**PLAN, "id": "plan-1"}],
            "sites": [{"site_key": "shopee_br", "display_name": "Shopee Brazil", "is_active": True}],
            "usage_logs": [
                {
                    "api_key_id": "key-1",
                    "created_at": NOW.isoformat(),
                    "status": "success",
                    "response_time_ms": 1600,
                    "request_url": "https://shopee.com.br/x-i.1.2",
                    "sites": {"site_key": "shopee_br"},
                },
                {
                    "api_key_id": "key-1",
                    "created_at": (NOW - timedelta(minutes=5)).isoformat(),
                    "status": "captcha_blocked",
                    "response_time_ms": 6400,
                    "request_url": "https://shopee.co.th/y-i.3.4",
                    "sites": {"site_key": "shopee_th"},
                },
            ],
        }

    def table(self, name):
        return _Query(self, name)


@pytest.fixture
def client(monkeypatch):
    fake = FakeSupabase()
    for module in ("app.core.web_auth", "app.core.web_stats", "app.routers.web"):
        monkeypatch.setattr(f"{module}.get_supabase", lambda fake=fake: fake)

    from app.main import app

    return TestClient(app, follow_redirects=False)


def _sign_in(client):
    response = client.post(
        "/login", content=f"api_key={RAW_KEY}&next=%2Fapp", headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert response.status_code == 303
    return response


def test_landing_renders_with_plans(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Marketplace product data" in response.text
    assert "starter" in response.text.lower()
    assert "$49" in response.text


def test_app_pages_redirect_to_login_when_signed_out(client):
    for path in ("/app", "/app/playground", "/app/keys"):
        response = client.get(path)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/login")


def test_login_rejects_unknown_key(client):
    response = client.post("/login", content="api_key=sk_nope&next=%2Fapp", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert response.status_code == 401
    assert "not recognised" in response.text


def test_login_sets_httponly_cookie_and_pages_render(client):
    response = _sign_in(client)
    assert "httponly" in response.headers["set-cookie"].lower()

    overview = client.get("/app")
    assert overview.status_code == 200
    assert "Acme Corp" in overview.text
    assert "shopee.com.br" in overview.text
    assert "Captcha" in overview.text

    assert client.get("/app/playground").status_code == 200
    keys = client.get("/app/keys")
    assert keys.status_code == 200
    assert "This key" in keys.text


def test_login_next_cannot_redirect_off_site(client):
    response = client.post(
        "/login", content=f"api_key={RAW_KEY}&next=https%3A%2F%2Fevil.example", headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert response.headers["location"] == "/app"


def test_revoke_other_key_but_not_current(client):
    _sign_in(client)
    assert client.post("/app/api/keys/key-1/revoke").status_code == 409
    assert client.post("/app/api/keys/key-2/revoke").status_code == 200
    assert client.post("/app/api/keys/does-not-exist/revoke").status_code == 404


def test_create_key_returns_raw_key_once(client):
    _sign_in(client)
    response = client.post("/app/api/keys")
    assert response.status_code == 200
    assert response.json()["api_key"].startswith("sk_")


def test_api_endpoints_require_session(client):
    assert client.post("/app/api/keys").status_code == 401
    assert client.post("/app/api/scrape", json={"site_key": "shopee_br", "url": "https://x"}).status_code == 401
