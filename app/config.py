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
    browser_mode: str = "local"
    brightdata_ws_endpoint: str = ""
    brightdata_api_token: str = ""

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

    # Optional: log into a real Shopee account once at startup and reuse that
    # session (cookies/localStorage) across scrape contexts instead of
    # scraping anonymously. Login failure is non-fatal — falls back to
    # anonymous scraping with a warning logged.
    shopee_login_enabled: bool = False
    shopee_login_username: str = ""
    shopee_login_password: str = ""

    default_requests_per_minute: int = 30

    # Cap on how many URLs a single POST /v1/{site_key}/pdp/batch call may
    # request — each URL still costs one burst-limit slot and one quota unit,
    # same as if it were its own request, so this bounds how much of a key's
    # per-minute/daily budget one call can spend atomically.
    max_batch_size: int = 20

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
