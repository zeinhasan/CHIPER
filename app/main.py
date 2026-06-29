"""
CHIPER — Content Integration, Parsing, and HTML Extraction Routine
FastAPI Application Entry Point
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from playwright.async_api import Browser, async_playwright
from prometheus_client import REGISTRY, generate_latest
from starlette.responses import Response

from app.api.routes import router
from app.config import settings
from app.middleware.tracing import TracingMiddleware
from app.utils.logging import get_logger, setup_logging
from app.utils.metrics import instrumentator

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager.

    On startup:
        - Configure structured JSON logging.
        - Launch Playwright and start a single shared Chromium browser instance.
    On shutdown:
        - Gracefully close the browser and stop Playwright.
    """

    # ── Startup ─────────────────────────────────────────────────────
    setup_logging()
    logger.info("CHIPER v2.5 starting up...")

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

    # ── Shutdown ────────────────────────────────────────────────────
    logger.info("Shutting down Playwright browser...")
    await browser.close()
    await pw.stop()
    logger.info("Playwright stopped. Goodbye!")


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

# ── Middleware ───────────────────────────────────────────────────────
app.add_middleware(TracingMiddleware)

# ── Prometheus HTTP Auto-Instrumentation ─────────────────────────────
instrumentator.instrument(app).expose(app, endpoint="/metrics", include_in_schema=True)

# ── API Routes ───────────────────────────────────────────────────────
app.include_router(router)


@app.get("/health", tags=["system"])
async def health_check() -> dict:
    """Simple health-check endpoint."""
    return {"status": "ok", "version": "2.5.0"}


# ── Direct run (development) ────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
