import json

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str
    supabase_service_role_key: str

    admin_dashboard_password: str = "change-me"

    playwright_headless: bool = True
    max_concurrent_scrapes: int = 3
    scrape_timeout_seconds: int = 30
    captcha_max_retries: int = 2

    # "local": launch Chromium in-process (default). "brightdata_cdp": connect
    # to Bright Data's Scraping Browser over CDP instead of launching locally —
    # it handles proxy rotation, fingerprinting, and CAPTCHA solving on Bright
    # Data's side, so BRIGHTDATA_WS_ENDPOINT replaces PROXY_MODE entirely (the
    # proxy_* settings below are ignored in this mode). "brightdata_unlocker_api":
    # no browser/Playwright involved at all — each scrape is a single POST to
    # Bright Data's Web Unlocker REST API, which renders the page server-side
    # and hands back plain HTML. Only meaningful for site adapters that
    # implement BaseScraper.fetch_pdp_via_unlocker_api (see shopee_br.py).
    # "brightdata_dataset_api": no browser/Playwright involved either — each
    # scrape triggers a job on one of Bright Data's maintained per-site
    # scrapers (their own infrastructure handles anti-bot/CAPTCHA entirely
    # server-side) via the async trigger/poll/snapshot Dataset API, and
    # returns already-structured JSON (price included) instead of raw HTML.
    # Needs BRIGHTDATA_SHOPEE_DATASET_ID below in addition to
    # BRIGHTDATA_API_TOKEN. Only meaningful for site adapters that implement
    # BaseScraper.fetch_pdp_via_dataset_api (see _shopee_common.py).
    browser_mode: str = "local"
    brightdata_ws_endpoint: str = ""
    brightdata_api_token: str = ""
    brightdata_shopee_dataset_id: str = ""

    # "apify": no browser/Playwright involved — each scrape is a single
    # synchronous POST to an Apify actor that scrapes the product server-side
    # (its own infrastructure handles anti-bot entirely) and returns
    # already-structured JSON directly, no separate poll/snapshot step needed
    # (unlike BROWSER_MODE=brightdata_dataset_api). Only meaningful for site
    # adapters that implement BaseScraper.fetch_pdp_via_apify (see
    # _shopee_common.py). Needs an Apify account/API token — apify.com.
    apify_api_token: str = ""
    # Actor slug in "owner/actor-name" form (converted to the API's
    # "owner~actor-name" form at call time) — gio21/shopee-product-detail is
    # a paid, per-successful-result actor confirmed to accept a direct
    # product URL and cover shopee.co.th; swap this if you pick a different
    # actor from Apify's marketplace.
    apify_shopee_product_detail_actor_id: str = "gio21/shopee-product-detail"

    # shopee_th's own browser_mode_override (see ShopeeTHScraper) — kept
    # separate from the global BROWSER_MODE above so this one site can route
    # differently (its persistent-profile local browser, or the Apify actor
    # above, or Bright Data's Dataset API) without affecting shopee_br/vn.
    # "local" is the persistent-profile + Patchright path this project has
    # relied on so far; every automated/headless variant of it still hits
    # Shopee TH's anti-bot layer (see shopee_login.py's comments) — "apify"
    # is worth trying once you have a token, since it avoids that fight
    # entirely by running server-side on Apify's own infrastructure.
    shopee_th_browser_mode_override: str = "local"

    # "static_list": round-robin PROXY_LIST. "rotating_session": one sticky
    # gateway (PROXY_GATEWAY_SERVER) with a fresh random session id appended
    # to the username on every request — the pattern residential-proxy
    # vendors (Bright Data, Oxylabs, Smartproxy/Decodo, IPRoyal, ...) use to
    # hand out a new exit IP per connection. "brightdata_unlocker": routes
    # through Bright Data's Web Unlocker product as a plain forward proxy —
    # Playwright still renders locally (so the existing network-capture
    # scraping strategy is unaffected), but Web Unlocker's own anti-bot
    # handling sits in front of every request/response through it. Requires
    # a separate "Unlocker"-type zone in Bright Data (not the Scraping
    # Browser zone used by BROWSER_MODE=brightdata_cdp), and only takes
    # effect when BROWSER_MODE=local.
    proxy_mode: str = "static_list"
    proxy_list: str = "[]"
    proxy_gateway_server: str = ""
    proxy_username_template: str = ""  # e.g. "user-session-{session}"
    proxy_password: str = ""

    brightdata_customer_id: str = ""
    brightdata_unlocker_zone: str = ""
    brightdata_unlocker_password: str = ""

    # "brightdata_residential" PROXY_MODE: a static residential/ISP proxy
    # zone (distinct from the Unlocker zone above), country-targeted per
    # request via a "-country-<cc>" username suffix built from each site
    # adapter's unlocker_country. Only takes effect when BROWSER_MODE=local.
    brightdata_residential_zone: str = ""
    brightdata_residential_password: str = ""

    # Optional: log into a real Shopee account once at startup and reuse that
    # session (cookies/localStorage) across scrape contexts instead of
    # scraping anonymously. Login failure is non-fatal — falls back to
    # anonymous scraping with a warning logged. This pair is shopee_br-specific;
    # see shopee_th_login_* below for shopee_th's own account.
    shopee_login_enabled: bool = False
    shopee_login_username: str = ""
    shopee_login_password: str = ""

    # Same as above but for shopee_th. Kept separate from shopee_login_* (not
    # a list/dict) because each site logs in with its own dedicated account —
    # ShopeeTHScraper.browser_mode_override forces shopee_th onto the
    # browser/local transport specifically so this cached session (plus
    # PROXY_MODE=brightdata_residential country targeting) is actually used
    # instead of bypassed like the brightdata_unlocker_api transport would.
    shopee_th_login_enabled: bool = False
    shopee_th_login_username: str = ""
    shopee_th_login_password: str = ""

    # shopee_th specifically (not br/vn): reuse one persistent Chromium
    # profile (browser_profiles/shopee_th/, on disk) across every scrape and
    # app restart, instead of a fresh throwaway context per request. Seed it
    # once with `python scripts/shopee_th_manual_login.py` — a real, visible
    # browser window you log into by hand — since every *automated* shopee_th
    # login attempt tried so far (local Chromium, Bright Data's Scraping
    # Browser) got blocked by Shopee's own traffic-verification wall before
    # completing. See browser_pool.py's _shopee_th_context.
    shopee_th_use_persistent_profile: bool = True

    # How often (minutes) to re-check whether the shopee_th persistent
    # profile's session is still actually authenticated, on top of the
    # once-at-startup check — a session can go stale hours into a long-running
    # process, and without this the first sign would be scrapes themselves
    # failing. See browser_pool.py's session watchdog / shopee_login.py's
    # check_shopee_th_session_valid.
    shopee_th_session_check_interval_minutes: int = 30
    # Optional: POST a small JSON payload ({"text": "..."}, Slack incoming
    # webhook-compatible) to this URL only when the session's valid/invalid
    # state actually changes (not on every check) — so you find out the
    # moment it needs scripts/shopee_th_manual_login.py rerun instead of only
    # seeing it in the logs. Leave empty to rely on the log warning alone.
    shopee_th_session_alert_webhook_url: str = ""

    default_requests_per_minute: int = 30

    # Cap on how many URLs a single POST /v1/{site_key}/pdp/batch call may
    # request — each URL still costs one burst-limit slot and one quota unit,
    # same as if it were its own request, so this bounds how much of a key's
    # per-minute/daily budget one call can spend atomically.
    max_batch_size: int = 20

    # Shared httpx.AsyncClient pool sizing for fetch_pdp_via_unlocker_api
    # (app/scrapers/http_pool.py) — bump these alongside MAX_CONCURRENT_SCRAPES
    # if running many concurrent Web Unlocker calls at once.
    http_pool_max_connections: int = 600
    http_pool_max_keepalive: int = 100

    # Whether a successful scrape's normalized PDPData also gets persisted to
    # the `pdp_data` table (in addition to the always-on `usage_logs` row).
    # Set to false to scrape without storing product data at all — e.g. a
    # client that only wants pass-through results, or while iterating on an
    # adapter without piling up test rows.
    store_pdp_data: bool = True

    log_dir: str = "logs"
    log_file: str = "app.log"
    log_max_bytes: int = 10_000_000
    log_backup_count: int = 5

    # Small randomized delay before each scrape attempt (including the first),
    # so concurrent requests don't all hit the target in lockstep. Backoff
    # between CAPTCHA retries also gets jitter added on top of its base delay.
    # Set both to 0 to disable.
    pre_scrape_jitter_ms_min: int = 150
    pre_scrape_jitter_ms_max: int = 500

    @property
    def proxies(self) -> list[str]:
        try:
            parsed = json.loads(self.proxy_list)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []


settings = Settings()
