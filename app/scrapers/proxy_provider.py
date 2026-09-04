"""Pluggable proxy sourcing. Three built-in modes, selected by PROXY_MODE in .env:

  1. "static_list" — PROXY_LIST is a JSON array of proxy URLs, cycled round-robin.
  2. "rotating_session" — a single sticky gateway (PROXY_GATEWAY_SERVER) with a
     fresh random session id appended to the username on every request. This is
     how residential-proxy vendors (Bright Data, Oxylabs, Smartproxy/Decodo,
     IPRoyal, ...) hand out a new exit IP per connection — reusing one IP across
     many requests is what gets it fingerprinted and CAPTCHA-walled.
  3. "brightdata_unlocker" — Bright Data's Web Unlocker product as a forward
     proxy (BRIGHTDATA_CUSTOMER_ID / BRIGHTDATA_UNLOCKER_ZONE /
     BRIGHTDATA_UNLOCKER_PASSWORD). Only takes effect under BROWSER_MODE=local.
  4. "brightdata_residential" — a static residential/ISP proxy zone
     (BRIGHTDATA_CUSTOMER_ID / BRIGHTDATA_RESIDENTIAL_ZONE /
     BRIGHTDATA_RESIDENTIAL_PASSWORD), country-targeted per request via a
     "-country-<cc>" username suffix built from the site adapter's
     unlocker_country. Only takes effect under BROWSER_MODE=local.

Wire up something else entirely by subclassing ProxyProvider and swapping it
into get_proxy_provider().
"""

import itertools
import secrets
from abc import ABC, abstractmethod
from functools import lru_cache
from urllib.parse import urlparse

from app.config import settings


class ProxyProvider(ABC):
    @abstractmethod
    def next_proxy(self, country: str = "") -> dict | None:
        """Return a Playwright-compatible proxy dict (`{"server", "username"?, "password"?}`) or None for no proxy.

        `country` is the target site's two-letter unlocker_country (e.g. "th") —
        only providers that support per-request country targeting use it.
        """
        raise NotImplementedError


def _parse_proxy_url(proxy_url: str) -> dict:
    # Credentials embedded in the URL (http://user:pass@host:port) are split
    # out into separate fields — Playwright's proxy option documents
    # username/password as distinct from the server, and not every proxy
    # vendor's auth is honored when credentials are left inline.
    parsed = urlparse(proxy_url)
    server = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}" if parsed.port else f"{parsed.scheme}://{parsed.hostname}"
    proxy: dict = {"server": server}
    if parsed.username:
        proxy["username"] = parsed.username
    if parsed.password:
        proxy["password"] = parsed.password
    return proxy


class StaticListProxyProvider(ProxyProvider):
    def __init__(self, proxy_urls: list[str]):
        self._cycle = itertools.cycle(proxy_urls) if proxy_urls else None

    def next_proxy(self, country: str = "") -> dict | None:
        if self._cycle is None:
            return None
        return _parse_proxy_url(next(self._cycle))


class RotatingSessionProxyProvider(ProxyProvider):
    def __init__(self, server: str, username_template: str, password: str):
        self._server = server
        self._username_template = username_template
        self._password = password

    def next_proxy(self, country: str = "") -> dict:
        session_id = secrets.token_hex(4)
        return {
            "server": self._server,
            "username": self._username_template.format(session=session_id),
            "password": self._password,
        }


class BrightDataUnlockerProxyProvider(ProxyProvider):
    """Bright Data's Web Unlocker, used as a plain forward proxy rather than
    its REST API — Playwright routes every request through it (initial
    document load and every subsequent XHR/asset) while still rendering
    locally, so callers keep full control of the page. No per-request
    session id needed; Web Unlocker manages IP/fingerprint rotation on its
    own side per request.
    """

    _SERVER = "http://brd.superproxy.io:44445"

    def __init__(self, customer_id: str, zone: str, password: str):
        self._username = f"brd-customer-{customer_id}-zone-{zone}"
        self._password = password

    def next_proxy(self, country: str = "") -> dict:
        return {"server": self._SERVER, "username": self._username, "password": self._password}


class BrightDataResidentialProxyProvider(ProxyProvider):
    """A static residential/ISP Bright Data zone, targeted per request via a
    "-country-<cc>" username suffix — unlike BrightDataUnlockerProxyProvider,
    the exit country varies per site adapter rather than being fixed for the
    whole zone, so next_proxy() takes it as a parameter.
    """

    _SERVER = "http://brd.superproxy.io:44445"

    def __init__(self, customer_id: str, zone: str, password: str):
        self._customer_id = customer_id
        self._zone = zone
        self._password = password

    def next_proxy(self, country: str = "") -> dict:
        username = f"brd-customer-{self._customer_id}-zone-{self._zone}"
        if country:
            username += f"-country-{country}"
        return {"server": self._SERVER, "username": username, "password": self._password}


@lru_cache
def get_proxy_provider() -> ProxyProvider:
    # Cached as a singleton: StaticListProxyProvider's round-robin only
    # advances if the same itertools.cycle instance is reused across calls.
    if settings.proxy_mode == "rotating_session":
        return RotatingSessionProxyProvider(
            server=settings.proxy_gateway_server,
            username_template=settings.proxy_username_template,
            password=settings.proxy_password,
        )
    if settings.proxy_mode == "brightdata_unlocker":
        return BrightDataUnlockerProxyProvider(
            customer_id=settings.brightdata_customer_id,
            zone=settings.brightdata_unlocker_zone,
            password=settings.brightdata_unlocker_password,
        )
    if settings.proxy_mode == "brightdata_residential":
        return BrightDataResidentialProxyProvider(
            customer_id=settings.brightdata_customer_id,
            zone=settings.brightdata_residential_zone,
            password=settings.brightdata_residential_password,
        )
    return StaticListProxyProvider(settings.proxies)
