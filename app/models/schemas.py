from datetime import datetime

from pydantic import BaseModel, Field


class ScrapeRequest(BaseModel):
    url: str


class BatchScrapeRequest(BaseModel):
    urls: list[str] = Field(..., min_length=1)


class PDPData(BaseModel):
    """Normalized shape every site adapter must produce.

    Fields common to virtually all product detail pages are top-level;
    anything site-specific goes in `raw` so adding a new site never
    requires a schema change.
    """

    site_key: str
    product_url: str
    external_product_id: str | None = None
    title: str | None = None
    price: float | None = None
    currency: str | None = None
    rating: float | None = None
    sold_count: int | None = None
    image_urls: list[str] = []
    raw: dict = {}
    scraped_at: datetime | None = None


class ScrapeResponse(BaseModel):
    """`data` is the site's own raw PDP payload verbatim — for Shopee, the
    whole get_pc envelope (error/error_msg/bff_meta/data), not just its
    `data` sub-object — no normalized/derived fields layered on top. Those
    derived fields (title, price, ...) still exist on PDPData internally for
    DB storage/dashboard use; they're just not part of the public response.
    """

    status: str
    data: dict | None = None
    error: str | None = None


class BatchScrapeItem(ScrapeResponse):
    url: str


class BatchScrapeResponse(BaseModel):
    results: list[BatchScrapeItem]


class AuthedKey(BaseModel):
    api_key_id: str
    client_id: str
    plan_id: str
    requests_per_minute: int
    daily_quota: int
    monthly_quota: int
