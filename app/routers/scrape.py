import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.core.auth import require_api_key
from app.core.rate_limiter import check_burst_limit, check_quota
from app.core.usage_logger import log_usage
from app.db.client import get_supabase
from app.deps import get_site_id
from app.models.schemas import AuthedKey, BatchScrapeItem, BatchScrapeRequest, BatchScrapeResponse, ScrapeRequest, ScrapeResponse
from app.scrapers.base import CaptchaBlockedError, ProductNotFoundError, ScraperError
from app.scrapers.registry import scrape_with_retries

router = APIRouter(prefix="/v1", tags=["scrape"])


def _insert_pdp_data(site_id: str, usage_log_id: str | None, pdp) -> None:
    get_supabase().table("pdp_data").insert(
        {
            "site_id": site_id,
            "usage_log_id": usage_log_id,
            "product_url": pdp.product_url,
            "external_product_id": pdp.external_product_id,
            "title": pdp.title,
            "price": pdp.price,
            "currency": pdp.currency,
            "rating": pdp.rating,
            "sold_count": pdp.sold_count,
            "image_urls": pdp.image_urls,
            "raw": pdp.raw,
        }
    ).execute()


async def _scrape_one(site_key: str, site_id: str, url: str, key: AuthedKey) -> BatchScrapeItem:
    """Runs the full auth-adjacent pipeline (rate limit, quota, scrape, usage
    logging) for one URL. Rate-limit/quota rejection is returned as a
    "rejected" item rather than raised, so a batch call can isolate one
    over-budget URL without failing every other URL in the same request.
    """
    try:
        check_burst_limit(key.api_key_id, key.requests_per_minute)
        await asyncio.to_thread(check_quota, key.api_key_id, key.daily_quota, key.monthly_quota)
    except HTTPException as exc:
        return BatchScrapeItem(url=url, status="rejected", error=str(exc.detail))

    started = time.monotonic()
    status = "failed"
    error_message: str | None = None
    pdp = None

    try:
        pdp = await scrape_with_retries(site_key, url)
        status = "success"
    except ProductNotFoundError:
        # The site confirmed the product doesn't exist (dead/removed listing)
        # rather than the scrape itself failing — counts as a successful
        # scrape with no data, not an error.
        status = "success"
    except CaptchaBlockedError as exc:
        status = "captcha_blocked"
        error_message = str(exc)
    except ScraperError as exc:
        status = "failed"
        error_message = str(exc)
    finally:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        usage_log_id = await asyncio.to_thread(log_usage, key.api_key_id, site_id, url, status, elapsed_ms)

    if pdp is not None:
        await asyncio.to_thread(_insert_pdp_data, site_id, usage_log_id, pdp)
        return BatchScrapeItem(url=url, status="success", data=pdp)

    return BatchScrapeItem(url=url, status=status, error=error_message)


@router.post("/{site_key}/pdp", response_model=ScrapeResponse)
async def scrape_pdp(
    site_key: str,
    body: ScrapeRequest,
    key: AuthedKey = Depends(require_api_key),
) -> ScrapeResponse:
    # Supabase's client is synchronous; every DB call below is offloaded via
    # asyncio.to_thread so a slow lookup/insert doesn't stall the event loop
    # for other concurrent requests.
    site_id = await asyncio.to_thread(get_site_id, site_key)
    if site_id is None:
        raise HTTPException(status_code=404, detail=f"Unsupported site: {site_key}")

    item = await _scrape_one(site_key, site_id, body.url, key)

    if item.status in ("success",):
        return ScrapeResponse(status="success", data=item.data)
    if item.status == "rejected":
        raise HTTPException(status_code=429, detail=item.error)

    status_code = 502 if item.status == "captcha_blocked" else 500
    raise HTTPException(status_code=status_code, detail=item.error or "Scrape failed")


@router.post("/{site_key}/pdp/batch", response_model=BatchScrapeResponse)
async def scrape_pdp_batch(
    site_key: str,
    body: BatchScrapeRequest,
    key: AuthedKey = Depends(require_api_key),
) -> BatchScrapeResponse:
    if len(body.urls) > settings.max_batch_size:
        raise HTTPException(
            status_code=422,
            detail=f"Batch too large: {len(body.urls)} URLs (max {settings.max_batch_size})",
        )

    site_id = await asyncio.to_thread(get_site_id, site_key)
    if site_id is None:
        raise HTTPException(status_code=404, detail=f"Unsupported site: {site_key}")

    # Each _scrape_one call acquires its own browser context; browser_pool's
    # semaphore (MAX_CONCURRENT_SCRAPES) already bounds real concurrency
    # across the whole app, so firing all of these at once is safe — extra
    # URLs simply queue for a context rather than piling up here.
    results = await asyncio.gather(*(_scrape_one(site_key, site_id, url, key) for url in body.urls))
    return BatchScrapeResponse(results=list(results))
