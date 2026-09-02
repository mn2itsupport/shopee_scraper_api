# Scraper API — Shopee Brazil PDP (multi-site ready)

A subscription-gated web scraping API. Ships with a Shopee Brazil product
detail page (PDP) adapter; the auth, rate limiting, quota, storage schema,
and usage dashboard are all generic so a second website is a small add,
not a rebuild.

**Before you point this at any site, check its Terms of Service and
robots.txt.** This project provides the technical scaffolding (auth, rate
limiting, storage, dashboard); staying within a site's terms is the
operator's responsibility.

See `docs/user_manual.html` for day-to-day operation (setup, provisioning
clients, calling the API, reading the dashboard) and
`docs/architecture.html` for how it's built internally (components, request
lifecycle, data model, extensibility). Open either directly in a browser.

## Stack

- FastAPI + Playwright (headless Chromium) for scraping
- Supabase (Postgres) for storage — API keys, usage logs, scraped data
- Server-rendered dashboard (Jinja2 + Chart.js) — no separate frontend

## Setup

```bash
cd shopee_scraper_api
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
playwright install chromium

cp .env.example .env   # fill in SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / ADMIN_DASHBOARD_PASSWORD
```

Apply `app/db/schema.sql` to your Supabase project (SQL editor, or `psql`/
`supabase db execute` against the connection string) — it creates the
`clients`, `plans`, `api_keys`, `sites`, `usage_logs`, and `pdp_data` tables
and seeds the `shopee_br` site row.

## Provisioning a client (manual subscription lifecycle)

```bash
python scripts/manage_keys.py create-plan --name starter --rpm 30 --daily-quota 1000 --monthly-quota 20000 --duration-days 30
python scripts/manage_keys.py create-client --name "Acme Corp" --email acme@example.com
python scripts/manage_keys.py create-key --client-email acme@example.com --plan-name starter
# prints the raw key once — give it to the client, only its hash is stored
```

`extend-key --key-prefix sk_xxx --days 30` renews a subscription;
`revoke-key --key-prefix sk_xxx` suspends it immediately. Once `expires_at`
passes, the API rejects that key automatically (no cron needed — checked on
every request).

## Running the API

```bash
uvicorn app.main:app --reload
```

- `POST /v1/shopee_br/pdp` with header `X-API-Key: sk_...` and body
  `{"url": "https://shopee.com.br/product-slug-i.123.456"}` → normalized PDP
  JSON.
- `GET /dashboard` — admin, all-clients usage (HTTP Basic auth,
  `ADMIN_DASHBOARD_PASSWORD`).
- `GET /dashboard/me?api_key=sk_...` — a client's own usage, no admin
  password needed.

Response shape (`ScrapeResponse` / `PDPData` in `app/models/schemas.py`):

```json
{
  "status": "success",
  "data": {
    "site_key": "shopee_br",
    "product_url": "...",
    "title": "...",
    "price": 129.90,
    "currency": "BRL",
    "rating": 4.8,
    "sold_count": 1200,
    "image_urls": ["..."],
    "raw": { "...site-specific fields..." }
  }
}
```

Errors: `401` invalid key, `403` suspended/expired subscription, `429`
burst/daily/monthly limit hit, `502` CAPTCHA wall after retries, `500`
other scrape failure.

## How a request is protected

1. **Auth** (`app/core/auth.py`) — API key hashed and looked up; rejects if
   missing, suspended, or past `expires_at`.
2. **Rate limiting** (`app/core/rate_limiter.py`) — an in-memory per-key
   burst window (requests/minute from the plan) plus a DB-backed daily/
   monthly quota check against `usage_logs`.
3. **Scraping** (`app/scrapers/`) — a shared Playwright browser with a
   capped number of concurrent contexts (`MAX_CONCURRENT_SCRAPES`); each
   request gets an isolated context with a randomized UA/viewport and an
   optional proxy from `proxy_provider.py`.
4. **CAPTCHA handling** (`app/scrapers/captcha.py`) — detects known
   anti-bot/verification signatures and retries with a fresh context (and
   rotated proxy) up to `CAPTCHA_MAX_RETRIES` times; if still blocked, the
   request fails as `captcha_blocked` rather than attempting to defeat the
   challenge. `CaptchaSolver` is a pluggable no-op hook if you want to wire
   a third-party solving service yourself.
5. **Logging** (`app/core/usage_logger.py`) — one row per request in
   `usage_logs` regardless of outcome; this is what both quota enforcement
   and the dashboard read from.

## Adding a second website

Everything above is generic. To add a new site:

1. Insert a row into `sites` (`site_key`, `display_name`, `base_domain`).
2. Create `app/scrapers/sites/<site>.py` implementing `BaseScraper`
   (see `shopee_br.py`) — return a `PDPData` with the common fields filled
   in and anything site-specific in `raw`.
3. Register it in `app/scrapers/registry.py`'s `SCRAPER_REGISTRY` dict.

No changes to auth, rate limiting, the database schema, or the dashboard.
The new site is immediately callable at `POST /v1/<site_key>/pdp`.

## Known limitations / next steps

- **Proxies**: `PROXY_MODE=static_list` (default) round-robins `PROXY_LIST`,
  a JSON array of proxy URLs. `PROXY_MODE=rotating_session` targets a
  residential-proxy vendor's sticky gateway (Bright Data, Oxylabs,
  Smartproxy/Decodo, IPRoyal, ...), minting a fresh random session id per
  request so each scrape gets a new exit IP — set `PROXY_GATEWAY_SERVER`,
  `PROXY_USERNAME_TEMPLATE` (must contain `{session}`), `PROXY_PASSWORD`.
  Subclass `ProxyProvider` in `app/scrapers/proxy_provider.py` for anything else.
- **Stealth**: every context is patched via `playwright-stealth`
  (`app/scrapers/browser_pool.py`) to mask common automation tells
  (`navigator.webdriver`, plugins, permissions, WebGL vendor, etc.) in
  addition to the randomized UA/viewport/locale and rotating proxy above.
  Combined, these reduce how often Shopee's anti-bot wall triggers — they
  don't eliminate it; `app/scrapers/captcha.py` still detects and retries
  when it does.
- **Rate limiting is single-instance**: the burst limiter is an in-memory
  dict. For multiple app instances, swap it for Redis (`INCR`+`EXPIRE`)
  behind the same `check_burst_limit` call site.
- **Billing**: subscription lifecycle (issue/extend/revoke) is manual via
  `scripts/manage_keys.py`. Wiring a payment provider webhook to call the
  same functions is the natural next step.
- **Dashboard aggregation** queries `usage_logs` directly and aggregates in
  Python; fine at MVP volume — move to a Postgres view/RPC if usage grows
  large.
- Shopee's internal PDP JSON API shape can change without notice; if the
  network-capture parser (`shopee_br.py`) starts returning mostly-empty
  `raw` fields, re-inspect the site's current XHR calls and adjust the
  field mapping.
