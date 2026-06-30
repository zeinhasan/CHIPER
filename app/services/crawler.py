"""
Recursive Crawl Engine (BFS)

Performs breadth-first multi-page crawl starting from a seed URL.
Reuses the existing Two-Tier scraping primitives from ``app.services.scraper``:
- ``_fetch_static``  — Tier-1 (httpx)
- ``_fetch_dynamic`` — Tier-2 (Playwright)
- ``_extract_markdown`` — HTML → Markdown

Link extraction is delegated to ``app.utils.links.extract_internal_links``.

Architecture::

    seed URL → BFS queue (depth-by-depth batches)
                │
                ├─ _scrape_page_and_extract_links()  ← Two-Tier
                │    └─ returns raw_html + markdown
                │
                ├─ extract_internal_links(html)  → child URLs
                │
                └─ enqueue children → next depth batch

Concurrency is gated by two nested semaphores:
- ``crawl_semaphore`` (total concurrent scrapes per crawl)
- ``playwright_semaphore`` (concurrent Playwright contexts; Tier-2 only)
"""

import asyncio
import itertools
import time
from collections import deque
from typing import Any, cast

import httpx
from playwright.async_api import Browser

from app.config import settings
from app.services.scraper import _extract_markdown, _fetch_dynamic, _fetch_static
from app.utils.helpers import get_logger
from app.utils.links import extract_internal_links, normalize_url

logger = get_logger(__name__)

# Per-depth delay (seconds) — applied once at the start of each depth batch,
# not between every individual page.  This is a gentler rate-limit that still
# prevents hammering the target server.
_DEPTH_BATCH_DELAY: float = 1.0


async def _scrape_page_and_extract_links(
    url: str,
    browser: Browser,
    client: httpx.AsyncClient,
    *,
    force_js_render: bool = False,
    playwright_semaphore: asyncio.Semaphore | None = None,
) -> dict[str, Any]:
    """
    Fetch a single page using the Two-Tier strategy and return both
    Markdown and raw HTML.

    Tier-1 (httpx) is tried first.  If it returns no HTML, Tier-2
    (Playwright) is used as fallback.  If *force_js_render* is True,
    Tier-1 is skipped entirely.

    Returns a dict with keys:
        url, fetch_method, markdown_content, title, content_length,
        raw_html (for link extraction), error
    """
    start = time.monotonic()

    result: dict[str, Any] = {
        "url": url,
        "fetch_method": "unknown",
        "markdown_content": "",
        "title": None,
        "content_length": 0,
        "raw_html": None,
        "error": None,
    }

    html: str | None = None
    error: str | None = None

    # ── Tier 1: Static (httpx) ─────────────────────────────────────
    if not force_js_render:
        html, error = await _fetch_static(url, client)
        if html is not None:
            result["fetch_method"] = "httpx"
            logger.debug("Crawl Tier-1 success", extra={"url": url})

    # ── Tier 2: Dynamic (Playwright) ───────────────────────────────
    if html is None:
        if playwright_semaphore is not None:
            async with playwright_semaphore:
                html, error = await _fetch_dynamic(url, browser)
        else:
            html, error = await _fetch_dynamic(url, browser)

        if html is not None:
            result["fetch_method"] = "playwright"
            logger.debug("Crawl Tier-2 success", extra={"url": url})
        else:
            result["fetch_method"] = "playwright-failed"
            result["error"] = error or "Both Tier-1 and Tier-2 returned no HTML"
            elapsed = time.monotonic() - start
            logger.warning(
                "Crawl page FAILED",
                extra={
                    "url": url,
                    "error": result["error"],
                    "duration_ms": int(elapsed * 1000),
                },
            )
            return result

    # ── Extract Markdown & store raw HTML ──────────────────────────
    if html is not None:
        markdown = _extract_markdown(html)
        result["markdown_content"] = markdown
        result["content_length"] = len(markdown)
        result["raw_html"] = html
        result["error"] = None

        elapsed = time.monotonic() - start
        logger.info(
            "Crawl page scraped",
            extra={
                "url": url,
                "method": result["fetch_method"],
                "content_length": result["content_length"],
                "duration_ms": int(elapsed * 1000),
            },
        )

    return result


async def crawl(
    seed_url: str,
    max_depth: int,
    max_pages: int,
    include_paths: list[str],
    exclude_paths: list[str],
    force_js_render: bool,
    http_client: httpx.AsyncClient,
    browsers: list[Browser],
) -> list[dict[str, Any]]:
    """
    BFS crawl starting from *seed_url*.

    Crawls breadth-first in depth-level batches.  All pages at the same
    depth are scraped concurrently (bounded by a semaphore), links are
    extracted from each page's raw HTML, and valid child URLs are enqueued
    for the next depth level.

    Returns a list of result dicts (one per successfully crawled page).
    Each dict matches the shape returned by ``_scrape_page_and_extract_links``
    plus ``depth`` and ``links_found`` (and minus ``raw_html``, which is
    stripped before returning).
    """
    if not browsers:
        logger.error("Browser pool is empty — cannot crawl.")
        return []

    # ── Normalize seed URL ─────────────────────────────────────────
    seed_normalized = normalize_url(seed_url)
    if not seed_normalized:
        logger.error(
            "Invalid seed URL after normalization", extra={"seed_url": seed_url}
        )
        return []

    visited: set[str] = {seed_normalized}
    queue: deque[tuple[str, int]] = deque([(seed_normalized, 0)])
    results: list[dict[str, Any]] = []

    # ── Semaphores ─────────────────────────────────────────────────
    crawl_semaphore = asyncio.Semaphore(settings.crawl_max_concurrent)
    playwright_semaphore = asyncio.Semaphore(3)  # cap Playwright contexts

    # Browser pool (round-robin)
    pool = itertools.cycle(browsers)

    logger.info(
        "Crawl started",
        extra={
            "seed_url": seed_normalized,
            "max_depth": max_depth,
            "max_pages": max_pages,
            "concurrency": settings.crawl_max_concurrent,
        },
    )

    # ── BFS Loop ───────────────────────────────────────────────────
    while queue and len(results) < max_pages:
        current_depth = queue[0][1]

        # Pop all URLs at the current depth level
        batch: list[tuple[str, int]] = []
        while (
            queue
            and queue[0][1] == current_depth
            and len(batch) + len(results) < max_pages
        ):
            batch.append(queue.popleft())

        if not batch:
            continue

        logger.info(
            "Crawl depth batch",
            extra={
                "depth": current_depth,
                "batch_size": len(batch),
                "total_crawled": len(results),
            },
        )

        # ── Optional inter-depth delay (rate limiting) ─────────────
        if current_depth > 0:
            await asyncio.sleep(_DEPTH_BATCH_DELAY)

        # ── Define per-page processor ──────────────────────────────
        async def _process_one(url: str, depth: int) -> dict[str, Any]:
            async with crawl_semaphore:
                # Per-page rate-limit delay
                if settings.crawl_delay_ms > 0:
                    await asyncio.sleep(settings.crawl_delay_ms / 1000.0)

                page = await _scrape_page_and_extract_links(
                    url,
                    next(pool),
                    http_client,
                    force_js_render=force_js_render,
                    playwright_semaphore=playwright_semaphore,
                )

                # ── Extract links from raw HTML ────────────────────
                links: list[str] = []
                if page.get("raw_html"):
                    links = extract_internal_links(
                        page["raw_html"],
                        url,
                        include_paths,
                        exclude_paths,
                    )

                page["links"] = links
                page["depth"] = depth
                page["links_found"] = len(links)
                # Remove raw HTML — it's large and not needed downstream
                page.pop("raw_html", None)
                return page

        # ── Scrape current depth batch concurrently ────────────────
        tasks = [_process_one(url, depth) for url, depth in batch]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        # ── Collect results & enqueue child links ──────────────────
        for r in batch_results:
            if isinstance(r, BaseException):
                logger.error(
                    "Crawl batch item raised exception",
                    extra={"error": str(r)},
                )
                continue

            # Narrowed: guaranteed dict[str, Any] after BaseException check
            page = cast(dict[str, Any], r)

            results.append(page)

            for child_url in page.get("links", []):
                child_norm = normalize_url(child_url)
                if not child_norm:
                    continue
                if child_norm in visited:
                    continue
                if page["depth"] + 1 > max_depth:
                    continue

                visited.add(child_norm)
                queue.append((child_norm, page["depth"] + 1))

    logger.info(
        "Crawl finished",
        extra={
            "seed_url": seed_normalized,
            "total_pages": len(results),
            "max_depth_reached": (max(r["depth"] for r in results) if results else 0),
        },
    )

    return results
