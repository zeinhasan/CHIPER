"""
CHIPER — Content Integration, Parsing, and HTML Extraction Routine
FastAPI Application Entry Point

Features:
- Shared httpx connection pool
- Browser pool (multiple Chromium instances, round-robin)
- Circuit breaker for SearXNG
- Rate limiting via slowapi
- Prometheus metrics + structured JSON logging
"""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import Browser, async_playwright
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.routes import limiter, router
from app.config import settings
from app.middleware.auth import ApiKeyMiddleware
from app.middleware.tracing import TracingMiddleware
from app.utils.circuit_breaker import CircuitBreaker
from app.utils.logging import get_logger, setup_logging
from app.utils.metrics import instrumentator

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager.

    On startup: logging, httpx pool, browser pool, circuit breaker.
    On shutdown: gracefully close all resources.
    """

    # --- Startup ---
    setup_logging()
    logger.info("CHIPER v1.4.2 starting up...")

    # Shared httpx client (connection pool reuse)
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(20.0),
        limits=httpx.Limits(
            max_connections=20,
            max_keepalive_connections=10,
        ),
    )
    logger.info(
        "Shared httpx.AsyncClient created "
        "(pool: 20 conn, 10 keepalive, max_body: %d MB).",
        settings.scrape_max_body_mb,
    )

    # Circuit breaker for SearXNG
    app.state.searxng_circuit = CircuitBreaker(
        name="searxng",
        failure_threshold=settings.circuit_breaker_failures,
        recovery_timeout=settings.circuit_breaker_timeout,
    )
    logger.info(
        "SearXNG circuit breaker initialized (failures=%d, timeout=%.0fs).",
        settings.circuit_breaker_failures,
        settings.circuit_breaker_timeout,
    )

    # Browser Pool: launch N Chromium instances for round-robin load distribution
    logger.info(
        "Starting Playwright + Browser Pool (size=%d)...", settings.browser_pool_size
    )
    pw = await async_playwright().start()

    launch_kwargs: dict = {"headless": True}
    if settings.playwright_browser_path:
        launch_kwargs["executable_path"] = settings.playwright_browser_path

    browsers: list[Browser] = []
    for i in range(settings.browser_pool_size):
        browser = await pw.chromium.launch(**launch_kwargs)
        browsers.append(browser)
        logger.info("Browser #%d/%d launched.", i + 1, settings.browser_pool_size)

    app.state.playwright = pw
    app.state.playwright_browsers = browsers
    logger.info("Browser pool ready: %d instances.", len(browsers))

    yield  # Application runs here

    # --- Shutdown ---
    logger.info("Shutting down browser pool (%d instances)...", len(browsers))
    for i, browser in enumerate(browsers):
        await browser.close()
        logger.info("Browser #%d closed.", i + 1)
    await pw.stop()
    logger.info("Playwright stopped.")

    logger.info("Closing shared httpx client...")
    await app.state.http_client.aclose()
    logger.info("httpx client closed. Goodbye!")


app = FastAPI(
    title="CHIPER — H.A.K.I.",
    description=(
        "Headless Asynchronous Knowledge Integrator. "
        "Middleware API that bridges AI agents with SearXNG for web research "
        "and content extraction, with optional AI summarization via DeepSeek."
    ),
    version="1.4.2",
    lifespan=lifespan,
)

# --- Middleware ---
app.add_middleware(TracingMiddleware)

# API Key Authentication (disabled when CHIPER_API_KEY is empty)
app.add_middleware(ApiKeyMiddleware)

# CORS — only add if explicitly configured (empty = no CORS)
if settings.chiper_cors_origins:
    origins = [o.strip() for o in settings.chiper_cors_origins.split(",") if o.strip()]
    if "*" in origins:
        logger.warning(
            "CORS allow_origins contains '*'.  This allows ANY website "
            "to call the API from a browser.  Consider restricting to "
            "specific origins in production via CHIPER_CORS_ORIGINS."
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST"],
        allow_headers=["X-API-Key", "X-Request-ID", "Content-Type"],
    )
    logger.info("CORS enabled for origins: %s", origins)
else:
    logger.info("CORS disabled (CHIPER_CORS_ORIGINS is empty).")

# --- Rate Limiting ---
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# --- Prometheus HTTP Auto-Instrumentation ---
instrumentator.instrument(app).expose(app, endpoint="/metrics", include_in_schema=True)

# --- API Routes ---
app.include_router(router)


@app.get("/health", tags=["system"])
async def health_check() -> dict:
    """Simple health-check endpoint."""
    return {"status": "ok", "version": "1.4.2"}


# --- Direct run (development) ---
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
