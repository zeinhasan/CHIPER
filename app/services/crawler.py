"""
Recursive Crawl Engine (BFS)

Performs breadth-first multi-page crawl starting from a seed URL.
Reuses the Two-Tier scraping from ``app.services.scraper.fetch_and_extract``.

Link extraction is delegated to ``app.utils.links.extract_internal_links``.

Architecture::

    seed URL → BFS queue (depth-by-depth batches)
                │
                ├─ fetch_and_extract()             ← Two-Tier (scraper)
                │    └─ returns markdown + raw_html
                │
                ├─ extract_internal_links(html)  → child URLs
                │
                └─ enqueue children → next depth batch
"""

import asyncio
import itertools
from collections import deque
from typing import Any, cast

import httpx
from playwright.async_api import Browser

from app.config import settings
from app.services.scraper import fetch_and_extract
from app.utils.helpers import get_logger
from app.utils.links import extract_internal_links, normalize_url

logger = get_logger(__name__)


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
    playwright_semaphore = asyncio.Semaphore(
        settings.crawl_playwright_concurrency
    )  # cap Playwright contexts

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
            await asyncio.sleep(settings.crawl_depth_delay)

        # ── Define per-page processor ──────────────────────────────
        async def _process_one(url: str, depth: int) -> dict[str, Any]:
            async with crawl_semaphore:
                # Per-page rate-limit delay
                if settings.crawl_delay_ms > 0:
                    await asyncio.sleep(settings.crawl_delay_ms / 1000.0)

                # Use shared fetch_and_extract with raw_html for link extraction
                page = await fetch_and_extract(
                    url,
                    next(pool),
                    http_client,
                    force_js_render=force_js_render,
                    semaphore=playwright_semaphore,
                    include_raw_html=True,
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
