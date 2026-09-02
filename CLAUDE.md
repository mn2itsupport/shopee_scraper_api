# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A subscription-gated web scraping API. Ships with Shopee product-detail-page
adapters for Brazil, Thailand, and Vietnam (`app/scrapers/sites/shopee_br.py`,
`shopee_th.py`, `shopee_vn.py` — thin country-specific subclasses of the
shared `ShopeeScraper` in `_shopee_common.py`), but auth, rate limiting, quota
tracking, storage schema, and the usage dashboard are all site-agnostic —
adding another Shopee country, or another site entirely, is a small,
additive change, not a rebuild.

**Before pointing this at any new site, check its Terms of Service and
robots.txt** — the scaffolding here is generic, staying within a site's
terms is the operator's responsibility.

Docs live in `docs/architecture.html` (internals: components, request
lifecycle, data model, extensibility) and `docs/user_manual.html`
(operation: setup, provisioning clients, calling the API, dashboard). Open
them directly in a browser.

## Stack

- FastAPI + Playwright (headless Chromium) for scraping
- Supabase (Postgres) for storage — API keys, usage logs, scraped data
- Server-rendered dashboard (Jinja2 + Chart.js), no separate frontend

There is currently no test suite and no lint/format tooling configured in
this repo — don't assume `pytest`/`ruff`/etc. exist; check before invoking
them.

## Commands

```bash
cd shopee_scraper_api
python -m venv .venv && source .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -r requirements.txt
playwright install chromium

cp .env.example .env   # fill in SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / ADMIN_DASHBOARD_PASSWORD
```

Apply `app/db/schema.sql` to the Supabase project (SQL editor, `psql`, or
`supabase db execute`) — creates `clients`, `plans`, `api_keys`, `sites`,
`usage_logs`, `pdp_data` and seeds the `shopee_br` site row.

Run the API:

```bash
uvicorn app.main:app --reload
```

Provision a client (manual subscription lifecycle, via `scripts/manage_keys.py`):

```bash
python scripts/manage_keys.py create-plan --name starter --rpm 30 --daily-quota 1000 --monthly-quota 20000 --duration-days 30
python scripts/manage_keys.py create-client --name "Acme Corp" --email acme@example.com
python scripts/manage_keys.py create-key --client-email acme@example.com --plan-name starter
# prints the raw key once — only its hash is stored
python scripts/manage_keys.py extend-key --key-prefix sk_xxx --days 30   # renew
python scripts/manage_keys.py revoke-key --key-prefix sk_xxx            # suspend immediately
```

Expired keys (`expires_at` passed) are rejected automatically on every
request — no cron needed.

Call the API:

```bash
curl -X POST http://localhost:8000/v1/shopee_br/pdp \
  -H "X-API-Key: sk_..." -H "Content-Type: application/json" \
  -d '{"url": "https://shopee.com.br/product-slug-i.123.456"}'
```

- `GET /dashboard` — admin, all-clients usage (HTTP Basic auth, `ADMIN_DASHBOARD_PASSWORD`).
- `GET /dashboard/me?api_key=sk_...` — a client's own usage, no admin password.

## Architecture: the request pipeline

`POST /v1/{site_key}/pdp` (`app/routers/scrape.py`) runs every request
through, in order:

1. **Auth** (`app/core/auth.py`, `require_api_key` dependency) — API key is
   hashed and looked up; rejects (401/403) if missing, suspended, or past
   `expires_at`.
2. **Rate limiting** (`app/core/rate_limiter.py`) — `check_burst_limit` is
   an **in-memory** per-key sliding window (requests/minute from the
   client's plan); `check_quota` is a DB-backed daily/monthly check against
   `usage_logs`. Burst limiting only works correctly for a single app
   instance — see Known limitations.
3. **Scraping** (`app/scrapers/registry.py::scrape_with_retries`) — looks up
   the adapter in `SCRAPER_REGISTRY`, runs it inside a fresh
   `browser_pool.acquire_context()`, and retries with a brand-new context
   (+ rotated proxy) up to `CAPTCHA_MAX_RETRIES` times on
   `CaptchaBlockedError` before giving up.
4. **Logging** (`app/core/usage_logger.py::log_usage`) — always runs (in a
   `finally`), one row per request in `usage_logs` regardless of outcome;
   this is what both quota enforcement and the dashboard read from.
5. On success, the normalized `PDPData` is also persisted to `pdp_data`
   (`scrape.py::_insert_pdp_data`) linked to the usage log row.

The Supabase client (`app/db/client.py`) is synchronous — every DB call in
the request path is wrapped in `asyncio.to_thread` so it doesn't block the
event loop for other concurrent requests. Keep that pattern when adding new
DB calls on the hot path.

## Architecture: the browser pool

`app/scrapers/browser_pool.py` starts **one shared Chromium instance** for
the whole process (`startup()`/`shutdown()`, wired into `main.py`'s
lifespan). Each scrape gets its own fresh `BrowserContext` — isolated
cookies/storage, randomized UA/viewport, `pt-BR` locale, a
`navigator.webdriver` patch — so requests never leak state between clients
or sites. Concurrency across all sites is capped by one semaphore sized
from `MAX_CONCURRENT_SCRAPES`; extra requests queue rather than spawning
unbounded contexts. `acquire_context()` is the only entry point adapters
should use to get a context.

## Architecture: adding a second website

Everything above is generic and requires no changes. To add a site:

1. Insert a row into the `sites` table (`site_key`, `display_name`, `base_domain`).
2. Create `app/scrapers/sites/<site>.py` implementing `BaseScraper`
   (`app/scrapers/base.py`) — model it on `shopee_br.py`. `fetch_pdp(context, url)`
   must return a `PDPData` with the common fields filled in and anything
   site-specific in `raw`; raise `CaptchaBlockedError` for an anti-bot wall
   and `ScraperError` for any other failure.
3. Register it as one line in `SCRAPER_REGISTRY` in `app/scrapers/registry.py`.

The new site is immediately callable at `POST /v1/<site_key>/pdp` — auth,
rate limiting, the DB schema, and the dashboard need no changes.

`_shopee_common.py::ShopeeScraper` (shared by all three Shopee country
adapters) parses Shopee's internal PDP JSON API via network capture rather
than scraping rendered HTML — if it starts returning mostly-empty `raw`
fields for a given country, that country's XHR shape has likely changed;
re-inspect and adjust the field mapping there. A new Shopee country is a
subclass of `ShopeeScraper` pinning `site_key`, `base_domain`,
`default_currency`, `locale`, `timezone_id`, and `geolocation` — see
`shopee_th.py` for the minimal shape.

## Known limitations (relevant when touching related code)

- **Proxies**: `PROXY_MODE=static_list` (default) round-robins `PROXY_LIST`
  (JSON array of proxy URLs, cached as a singleton via `get_proxy_provider`
  so the round-robin cursor actually persists across requests — don't
  reintroduce a per-call provider instance, it silently breaks rotation).
  `PROXY_MODE=rotating_session` targets a residential-proxy vendor's sticky
  gateway, generating a fresh session id (→ fresh exit IP) per request; see
  `RotatingSessionProxyProvider` in `app/scrapers/proxy_provider.py`.
- **Stealth**: `browser_pool.py` applies `playwright-stealth`
  (`Stealth().apply_stealth_async`) to every context. This plus the rotating
  proxy reduces CAPTCHA frequency but doesn't eliminate it — `captcha.py`
  still needs to detect and retry.
- **Rate limiting is single-instance**: `check_burst_limit` uses an
  in-memory dict. Scaling to multiple app instances requires swapping it
  for Redis (`INCR`+`EXPIRE`) behind the same call site.
- **Billing** is manual via `scripts/manage_keys.py`; a payment webhook
  would call the same underlying functions.
- **Dashboard aggregation** queries `usage_logs` directly and aggregates in
  Python (`app/routers/dashboard_api.py`) — fine at MVP volume, move to a
  Postgres view/RPC if usage grows.
- `CaptchaSolver` (`app/scrapers/captcha.py`) is a pluggable no-op hook —
  the app deliberately fails closed (`captcha_blocked`) rather than
  attempting to defeat a challenge unless a real solver is wired in.
