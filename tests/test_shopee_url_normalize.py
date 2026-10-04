"""_normalize_shopee_url is shared by every Shopee country adapter — these
pin both the new get_pc API URL rewrite and that ordinary product URLs
(Brazil's included) come back exactly as before.
"""

import os

import pytest

os.environ.setdefault("SUPABASE_URL", "http://example.invalid")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from app.scrapers.sites._shopee_common import _normalize_shopee_url  # noqa: E402


@pytest.mark.parametrize(
    "url",
    [
        "https://shopee.com.br/product-slug-i.123.456",
        "https://shopee.com.br/Kit-Camisetas-Masculinas-i.1234567.89012345678?sp_atk=abc&xptdk=def",
        "https://shopee.co.th/product-i.481607585.11168850119",
        "https://shopee.vn/Ao-thun-i.111.222",
        "https://shopee.com.br/search?keyword=camiseta",
        "https://shopee.com.br/api/v4/item/get?itemid=1&shopid=2",
    ],
)
def test_other_urls_unchanged(url):
    assert _normalize_shopee_url(url) == url


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://shopee.com.br/product/123/456", "https://shopee.com.br/product-i.123.456"),
        ("https://shopee.co.th/product/5758626/9118258234?sp_atk=x", "https://shopee.co.th/product-i.5758626.9118258234"),
    ],
)
def test_alt_product_path_still_rewritten(url, expected):
    assert _normalize_shopee_url(url) == expected


@pytest.mark.parametrize(
    "url, expected",
    [
        (
            "https://shopee.co.th/api/v4/pdp/get_pc?display_model_id=262978411165&item_id=48268385985"
            "&model_selection_logic=3&shop_id=446091597&tz_offset_in_minutes=330&detail_level=0",
            "https://shopee.co.th/product-i.446091597.48268385985",
        ),
        (
            "https://shopee.com.br/api/v4/pdp/get_pc?shop_id=123&item_id=456",
            "https://shopee.com.br/product-i.123.456",
        ),
    ],
)
def test_get_pc_api_url_rewritten(url, expected):
    assert _normalize_shopee_url(url) == expected


def test_get_pc_api_url_without_ids_unchanged():
    url = "https://shopee.co.th/api/v4/pdp/get_pc?detail_level=0"
    assert _normalize_shopee_url(url) == url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("shopee.co.th/product/36957691/7915998466", "https://shopee.co.th/product-i.36957691.7915998466"),
        ("shopee.co.th/product-i.481607585.11168850119", "https://shopee.co.th/product-i.481607585.11168850119"),
        ("  //shopee.com.br/product-slug-i.123.456 ", "https://shopee.com.br/product-slug-i.123.456"),
    ],
)
def test_missing_scheme_gets_https(url, expected):
    assert _normalize_shopee_url(url) == expected
