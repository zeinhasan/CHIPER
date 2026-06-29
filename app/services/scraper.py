"""
Two-Tier Scraping Engine

Tier 1 (Fast Path - Static): httpx → trafilatura
Tier 2 (Deep Path - Dynamic): Playwright headless browser → trafilatura

Falls back from Tier 1 to Tier 2 when:
  - Tier 1 returns insufficient content (< min_content_length chars)
  - Tier 1 content is detected as JavaScript garbage (SPA without JS rendering)
  - force_js_render flag is explicitly set

Features retry with exponential backoff for resilient fetching.
Instrumented with Prometheus metrics for observability.
"""

import asyncio
import re
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

# Retry configuration
STATIC_RETRIES: int = settings.fetch_static_retries
DYNAMIC_RETRIES: int = settings.fetch_dynamic_retries
RETRY_BASE_DELAY: float = settings.fetch_retry_base_delay

# Blocks unnecessary resources in Playwright to speed up rendering.
BLOCKED_RESOURCES: list[str] = ["image", "media", "font", "stylesheet"]

# Regex patterns that indicate JavaScript code (not article text).
_JS_PATTERNS: list[str] = [
    r"\bfunction\b",
    r"\bconst\b",
    r"\blet\b",
    r"\bvar\b",
    r"\.getElementById\b",
    r"\.querySelector\b",
    r"\.addEventListener\b",
    r"\bwindow\.",
    r"\bdocument\.",
    r"\bconsole\.",
    r"\btypeof\b",
    r"!function\b",
    r"\bObject\.defineProperty\b",
    r"\bXMLHttpRequest\b",
    r"\bfetch\s*\(",
    r"\bvoid\s+0\b",
    r"Error\s*\(",
    r"catch\s*\(",
    r"\.prototype\.",
    r"performance\.now\b",
]

# Density threshold: JS patterns per 1000 characters.
_JS_DENSITY_THRESHOLD: float = 10.0


def _is_javascript_garbage(content: str) -> bool:
    """
    Detect if extracted content is mostly JavaScript code rather than
    human-readable article text (e.g. from a JS-heavy SPA fetched statically).
    """
    if not content or len(content) < 200:
        return False

    text = content.lower()

    # Count JavaScript-indicative patterns
    js_hits = 0
    for pattern in _JS_PATTERNS:
        js_hits += len(re.findall(pattern, text))

    # Calculate density: JS patterns per 1000 characters
    kb = len(content) / 1000.0
    density = js_hits / kb if kb > 0 else 0

    if density > _JS_DENSITY_THRESHOLD:
        return True

    # Heuristic: lots of semicolons + no paragraph breaks = code
    semicolons = text.count(";")
    newlines = text.count("\n")
    if newlines < 5 and semicolons > 50:
        return True

    return False


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


async def _fetch_static(
    url: str,
    client: httpx.AsyncClient,
) -> tuple[str | None, str | None]:
    """
    Tier 1: Fetch URL with httpx (static HTML, no JavaScript).

    Retries up to STATIC_RETRIES times with exponential backoff.

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

    last_error = None

    for attempt in range(1, STATIC_RETRIES + 1):
        try:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.text, None
        except httpx.HTTPStatusError as exc:
            # Don't retry client errors (4xx)
            if 400 <= exc.response.status_code < 500:
                return None, f"HTTP {exc.response.status_code}"
            last_error = f"HTTP {exc.response.status_code}"
        except httpx.RequestError as exc:
            last_error = f"Request error: {exc}"

        if attempt < STATIC_RETRIES:
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logger.debug(
                "httpx retry %d/%d for %s in %.1fs (%s)",
                attempt,
                STATIC_RETRIES,
                url,
                delay,
                last_error,
            )
            await asyncio.sleep(delay)

    return None, last_error


async def _fetch_dynamic(
    url: str,
    browser: Browser,
) -> tuple[str | None, str | None]:
    """
    Tier 2: Fetch URL with Playwright headless Chromium (JavaScript-rendered).

    Retries up to DYNAMIC_RETRIES times with exponential backoff.
    Blocks images/CSS/fonts to speed up rendering.

    Returns:
        (html, error_message) — exactly one will be None.
    """
    last_error = None

    for attempt in range(1, DYNAMIC_RETRIES + 1):
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 720},
        )

        await context.route("**/*", _block_unnecessary)
        page = await context.new_page()

        try:
            logger.debug("Playwright navigating", extra={"url": url})
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                logger.debug(
                    "networkidle timeout; continuing with current DOM",
                    extra={"url": url},
                )

            html = await page.content()
            return html, None

        except Exception as exc:
            last_error = f"Playwright error: {exc}"

            if attempt < DYNAMIC_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.debug(
                    "playwright retry %d/%d for %s in %.1fs (%s)",
                    attempt,
                    DYNAMIC_RETRIES,
                    url,
                    delay,
                    last_error,
                )
                await asyncio.sleep(delay)
        finally:
            await page.close()
            await context.close()

    return None, last_error


async def _block_unnecessary(route):
    """Route handler that blocks images, media, fonts, and stylesheets."""
    if route.request.resource_type in BLOCKED_RESOURCES:
        await route.abort()
    else:
        await route.continue_()


async def fetch_and_extract(
    url: str,
    browser: Browser,
    client: httpx.AsyncClient,
    *,
    force_js_render: bool = False,
    semaphore: asyncio.Semaphore | None = None,
) -> dict:
    """
    Scrape a single URL using the Two-Tier strategy.

    1. Tier 1: Try static fetch with httpx (with retry + backoff).
    2. If content is too short, is JS garbage, or force_js_render=True,
       escalate to Tier 2 (Playwright, with retry + backoff).
    3. Extract Markdown from the final HTML.

    Args:
        url: Target URL to scrape.
        browser: Playwright Browser instance (shared across requests).
        client: Shared httpx.AsyncClient (connection pool reuse).
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
        html, error = await _fetch_static(url, client)

        if html:
            markdown = _extract_markdown(html)
            content_len = len(markdown)

            # ── Quality gate ────────────────────────────────────────
            if content_len >= MIN_CONTENT_LENGTH and not _is_javascript_garbage(
                markdown
            ):
                elapsed = time.monotonic() - start
                result["fetch_method"] = "httpx"
                result["markdown_content"] = markdown
                result["content_length"] = content_len
                result["error"] = None

                scrape_duration.labels(fetch_method="httpx").observe(elapsed)
                scrape_total.labels(fetch_method="httpx", status="success").inc()

                logger.info(
                    "Tier-1 (httpx) SUCCESS",
                    extra={
                        "url": url,
                        "content_length": content_len,
                        "duration_ms": int(elapsed * 1000),
                    },
                )
                return result

            if content_len < MIN_CONTENT_LENGTH:
                logger.info(
                    "Tier-1 (httpx) low content, escalating to Tier-2",
                    extra={"url": url, "content_length": content_len},
                )
            elif _is_javascript_garbage(markdown):
                logger.info(
                    "Tier-1 (httpx) JS garbage detected, escalating to Tier-2",
                    extra={"url": url, "content_length": content_len},
                )
        else:
            logger.info(
                "Tier-1 (httpx) FAILED after retries, escalating to Tier-2",
                extra={"url": url, "error": error},
            )

    # --- Tier 2: Dynamic (Playwright) ---
    if not force_js_render:
        tier_fallback.inc()

    async def _run_tier2():
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
    client: httpx.AsyncClient,
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
        client: Shared httpx.AsyncClient (connection pool reuse).
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
            url,
            browser,
            client,
            force_js_render=force_js_render,
            semaphore=semaphore,
        )
        for url in urls
    ]

    logger.info(
        "Scraping URLs concurrently",
        extra={"url_count": len(urls), "force_js": force_js_render},
    )
    results = await asyncio.gather(*tasks)
    return list(results)
