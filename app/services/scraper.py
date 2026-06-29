"""
Two-Tier Scraping Engine

Tier 1 (Fast Path - Static): httpx → trafilatura
Tier 2 (Deep Path - Dynamic): Playwright headless browser → trafilatura

Falls back from Tier 1 to Tier 2 when:
  - Tier 1 returns insufficient content (< min_content_length chars)
  - force_js_render flag is explicitly set

Instrumented with Prometheus metrics for observability.
"""

import asyncio
import time

import httpx
import trafilatura
from markdownify import markdownify as md
from playwright.async_api import Browser

from app.config import settings
from app.utils.helpers import get_logger
from app.utils.metrics import (
    scrape_duration,
    scrape_total,
    tier_fallback,
)

logger = get_logger(__name__)


# Minimal content length (characters) before Tier-1 is considered "good enough".
MIN_CONTENT_LENGTH: int = settings.min_content_length

# Blocks unnecessary resources in Playwright to speed up rendering.
BLOCKED_RESOURCES: list[str] = ["image", "media", "font", "stylesheet"]


def _extract_markdown(html: str) -> str:
    """
    Extract clean, readable content from raw HTML.

    Uses trafilatura as the primary extractor. Falls back to markdownify
    if trafilatura returns nothing (e.g. very small pages).
    """
    raw_html = html or ""

    # Primary: trafilatura (removes boilerplate, nav, ads, etc.)
    extracted = trafilatura.extract(
        raw_html,
        output_format="markdown",
        with_metadata=True,
        favor_precision=True,
    )

    if extracted and len(extracted.strip()) >= 50:
        return extracted.strip()

    # Fallback: markdownify (best-effort HTML → Markdown conversion)
    logger.debug(
        "trafilatura returned short/empty output; falling back to markdownify."
    )
    fallback = md(
        raw_html,
        heading_style="ATX",
        strip=["script", "style", "nav", "footer", "iframe"],
    )
    return fallback.strip() if fallback else ""


async def _fetch_static(url: str) -> tuple[str | None, str | None]:
    """
    Tier 1: Fetch URL with httpx (static HTML, no JavaScript).

    Returns:
        (html, error_message) — exactly one will be None.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    async with httpx.AsyncClient(
        timeout=20.0, follow_redirects=True, headers=headers
    ) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text, None
        except httpx.HTTPStatusError as exc:
            msg = f"HTTP {exc.response.status_code}"
            return None, msg
        except httpx.RequestError as exc:
            msg = f"Request error: {exc}"
            return None, msg


async def _fetch_dynamic(url: str, browser: Browser) -> tuple[str | None, str | None]:
    """
    Tier 2: Fetch URL with Playwright headless Chromium (JavaScript-rendered).

    Blocks images/CSS/fonts to speed up rendering. Waits for network idle
    then extracts the full rendered HTML.

    Returns:
        (html, error_message) — exactly one will be None.
    """
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 720},
    )

    # Block heavy resources for faster loads.
    await context.route("**/*", _block_unnecessary)

    page = await context.new_page()

    try:
        logger.debug("Playwright navigating", extra={"url": url})
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        # Wait for network to settle (up to 10 seconds).
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            logger.debug(
                "networkidle timeout; continuing with current DOM", extra={"url": url}
            )

        html = await page.content()
        return html, None

    except Exception as exc:
        msg = f"Playwright error: {exc}"
        return None, msg

    finally:
        await page.close()
        await context.close()


async def _block_unnecessary(route):
    """Route handler that blocks images, media, fonts, and stylesheets."""
    if route.request.resource_type in BLOCKED_RESOURCES:
        await route.abort()
    else:
        await route.continue_()


async def fetch_and_extract(
    url: str,
    browser: Browser,
    *,
    force_js_render: bool = False,
    semaphore: asyncio.Semaphore | None = None,
) -> dict:
    """
    Scrape a single URL using the Two-Tier strategy.

    1. Tier 1: Try static fetch with httpx.
    2. If content is too short (or force_js_render=True), escalate to Tier 2 (Playwright).
    3. Extract Markdown from the final HTML.

    Args:
        url: Target URL to scrape.
        browser: Playwright Browser instance (shared across requests).
        force_js_render: If True, skip Tier 1 entirely.
        semaphore: Optional semaphore to limit concurrent Playwright instances.

    Returns:
        Dict with keys: url, fetch_method, markdown_content, title, content_length, error.
    """
    start = time.monotonic()

    result: dict = {
        "url": url,
        "fetch_method": "unknown",
        "markdown_content": "",
        "title": None,
        "content_length": 0,
        "error": None,
    }

    # --- Tier 1: Static (httpx) ---
    if not force_js_render:
        html, error = await _fetch_static(url)

        if html:
            markdown = _extract_markdown(html)

            if len(markdown) >= MIN_CONTENT_LENGTH:
                elapsed = time.monotonic() - start
                result["fetch_method"] = "httpx"
                result["markdown_content"] = markdown
                result["content_length"] = len(markdown)
                result["error"] = None

                scrape_duration.labels(fetch_method="httpx").observe(elapsed)
                scrape_total.labels(fetch_method="httpx", status="success").inc()

                logger.info(
                    "Tier-1 (httpx) SUCCESS",
                    extra={
                        "url": url,
                        "content_length": len(markdown),
                        "duration_ms": int(elapsed * 1000),
                    },
                )
                return result

            logger.info(
                "Tier-1 (httpx) low content, escalating to Tier-2",
                extra={"url": url, "content_length": len(markdown)},
            )
        else:
            logger.info(
                "Tier-1 (httpx) FAILED, escalating to Tier-2", extra={"url": url}
            )

    # --- Tier 2: Dynamic (Playwright) ---
    if not force_js_render:
        tier_fallback.inc()

    async def _run_tier2():
        t2_start = time.monotonic()

        html, error = await _fetch_dynamic(url, browser)

        if html:
            markdown = _extract_markdown(html)
            elapsed = time.monotonic() - start
            result["fetch_method"] = "playwright"
            result["markdown_content"] = markdown
            result["content_length"] = len(markdown)
            result["error"] = None

            scrape_duration.labels(fetch_method="playwright").observe(elapsed)
            scrape_total.labels(fetch_method="playwright", status="success").inc()

            logger.info(
                "Tier-2 (playwright) SUCCESS",
                extra={
                    "url": url,
                    "content_length": len(markdown),
                    "duration_ms": int(elapsed * 1000),
                },
            )
        else:
            elapsed = time.monotonic() - start
            result["fetch_method"] = "playwright-failed"
            result["markdown_content"] = ""
            result["error"] = error or "Playwright returned empty HTML"

            scrape_duration.labels(fetch_method="playwright-failed").observe(elapsed)
            scrape_total.labels(
                fetch_method="playwright-failed", status="failure"
            ).inc()

            logger.warning(
                "Tier-2 (playwright) FAILED",
                extra={"url": url, "error": result["error"]},
            )

    if semaphore:
        async with semaphore:
            await _run_tier2()
    else:
        await _run_tier2()

    return result


async def scrape_urls(
    urls: list[str],
    browser: Browser,
    *,
    force_js_render: bool = False,
    max_concurrent_playwright: int = 3,
) -> list[dict]:
    """
    Scrape multiple URLs concurrently using the Two-Tier strategy.

    Uses asyncio.gather for parallelism. A semaphore limits the number
    of simultaneous Playwright instances to avoid overwhelming the browser.

    Args:
        urls: List of URLs to scrape.
        browser: Shared Playwright Browser instance.
        force_js_render: If True, skip Tier-1 for all URLs.
        max_concurrent_playwright: Max concurrent Playwright browser contexts.

    Returns:
        List of result dicts (one per URL).
    """
    if not urls:
        return []

    semaphore = asyncio.Semaphore(max_concurrent_playwright)

    tasks = [
        fetch_and_extract(
            url, browser, force_js_render=force_js_render, semaphore=semaphore
        )
        for url in urls
    ]

    logger.info(
        "Scraping URLs concurrently",
        extra={"url_count": len(urls), "force_js": force_js_render},
    )
    results = await asyncio.gather(*tasks)
    return list(results)
