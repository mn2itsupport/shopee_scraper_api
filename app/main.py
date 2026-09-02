import asyncio
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI

from app.config import settings
from app.routers import dashboard_api, dashboard_pages, scrape
from app.scrapers import browser_pool, http_pool

# uvicorn only configures its own "uvicorn"/"uvicorn.error"/"uvicorn.access"
# loggers — app.* loggers (e.g. shopee_login, browser_pool) have no handler
# by default, so INFO logs are silently dropped and WARNING+ only reach
# Python's unformatted "handler of last resort". Configure the root logger
# so app logs show up both in the server output and on disk.
os.makedirs(settings.log_dir, exist_ok=True)
_log_format = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_log_format)

_file_handler = RotatingFileHandler(
    os.path.join(settings.log_dir, settings.log_file),
    maxBytes=settings.log_max_bytes,
    backupCount=settings.log_backup_count,
    encoding="utf-8",
)
_file_handler.setFormatter(_log_format)

logging.basicConfig(level=logging.INFO, handlers=[_console_handler, _file_handler])

if sys.platform == "win32":
    # uvicorn --reload's subprocess supervisor can leave the default event
    # loop as SelectorEventLoop on Windows, which can't spawn subprocesses —
    # Playwright launches Chromium as one, so force Proactor explicitly.
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Every request offloads a few synchronous Supabase calls (quota check,
    # usage log, pdp_data insert) via asyncio.to_thread onto this loop's
    # default executor. Python's default size (min(32, cpu_count+4)) becomes
    # a real queueing bottleneck once dozens of requests are in flight
    # concurrently — size it to actually track MAX_CONCURRENT_SCRAPES-scale
    # throughput instead.
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=100))
    http_pool.startup()
    await browser_pool.startup()
    try:
        yield
    finally:
        await browser_pool.shutdown()
        await http_pool.shutdown()


app = FastAPI(title="Scraper API", version="0.1.0", lifespan=lifespan)

app.include_router(scrape.router)
app.include_router(dashboard_api.router)
app.include_router(dashboard_pages.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
