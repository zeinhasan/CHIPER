"""
CHIPER — Content Integration, Parsing, and HTML Extraction Routine
FastAPI Application Entry Point
"""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from playwright.async_api import Browser, async_playwright
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.routes import limiter, router
from app.config import settings
from app.middleware.tracing import TracingMiddleware
from app.utils.circuit_breaker import CircuitBreaker
from app.utils.logging import get_logger, setup_logging
from app.utils.metrics import instrumentator

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager.

    On startup:
        - Configure structured JSON logging.
        - Create shared httpx.AsyncClient (connection pool reuse).
        - Launch Playwright (shared Chromium instance).
        - Initialize circuit breaker for SearXNG.
    On shutdown:
        - Gracefully close browser, HTTP client, and Playwright.
    """

    # --- Startup ---
    setup_logging()
    logger.info("CHIPER v2.5 starting up...")

    # Shared httpx client (connection pool reuse)
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(20.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )
    logger.info("Shared httpx.AsyncClient created (pool: 20 conn, 10 keepalive).")

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

    # Playwright
    logger.info("Starting Playwright...")
    pw = await async_playwright().start()

    launch_kwargs: dict = {"headless": True}
    if settings.playwright_browser_path:
        launch_kwargs["executable_path"] = settings.playwright_browser_path

    browser: Browser = await pw.chromium.launch(**launch_kwargs)
    app.state.playwright = pw
    app.state.playwright_browser = browser
    logger.info("Playwright Chromium browser launched (headless).")

    yield  # Application runs here

    # --- Shutdown ---
    logger.info("Shutting down Playwright browser...")
    await browser.close()
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
    version="2.5.0",
    lifespan=lifespan,
)

# --- Middleware ---
app.add_middleware(TracingMiddleware)

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
    return {"status": "ok", "version": "2.5.0"}


# --- Direct run (development) ---
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
