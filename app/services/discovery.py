"""
Site Map Discovery Engine

Orchestrates URL discovery via three methods:
- **sitemap**  — Parse sitemap.xml (fast, relies on site's sitemap)
- **crawl**    — Lightweight BFS link-follower (no content scraping)
- **hybrid**   — Sitemap first, then crawl to fill gaps (default)

The crawl mode reuses link-extraction utilities from ``app.utils.links``
and is intentionally lightweight: only GETs HTML, extracts ``<a href>``
tags, and moves on — no JS rendering, no Markdown conversion.
"""

import asyncio
import time
from collections import deque
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.config import settings
from app.utils.helpers import get_logger
from app.utils.links import (
    _is_internal,
    _matches_filters,
    extract_internal_links,
    normalize_url,
)
from app.utils.security import is_safe_url
from app.utils.sitemap import find_sitemap_url, parse_sitemap

logger = get_logger(__name__)

# ────────────────────────────────────────────────────────────────────────
# Helper: fetch HTML and extract internal links (no content scraping)
# ────────────────────────────────────────────────────────────────────────


async def _fetch_and_extract_links(
    url: str,
    client: httpx.AsyncClient,
    include_paths: list[str],
    exclude_paths: list[str],
    include_subdomains: bool,
) -> tuple[list[str], str | None, str]:
    """
    GET *url*, extract internal same-domain links from the HTML.

    Returns (links, error_message, resolved_url).
    *resolved_url* is the final URL after following redirects.
    On HTTP errors the list is empty and the error string is set;
    resolved_url falls back to the original *url*.

    This is the **lightweight** counterpart of ``fetch_and_extract``
    in the crawl engine — no Playwright, no Markdown, no content extraction.
    """
    resolved_url = url  # fallback
    max_body = settings.scrape_max_body_mb * 1024 * 1024
    try:
        async with client.stream("GET", url, follow_redirects=True) as resp:
            resp.raise_for_status()
            resolved_url = str(resp.url)
            # ── SSRF re-check on post-redirect URL ────────────────
            # follow_redirects=True can land on an internal host even when
            # the original URL was safe. Reject before reading the body.
            if not is_safe_url(resolved_url):
                return [], "Redirect to internal/blocked address", url

            declared = resp.headers.get("content-length")
            if declared:
                try:
                    if int(declared) > max_body:
                        return (
                            [],
                            f"Response too large ({declared} bytes)",
                            resolved_url,
                        )
                except ValueError:
                    pass

            # ── Streamed body-size enforcement (chunked responses) ───
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > max_body:
                    return [], f"Response too large (>{max_body} bytes)", resolved_url
                chunks.append(chunk)
            html = b"".join(chunks).decode(
                resp.encoding or "utf-8", errors="replace"
            )
    except httpx.HTTPStatusError as exc:
        return [], f"HTTP {exc.response.status_code}", resolved_url
    except Exception as exc:
        return [], str(exc), resolved_url

    if not html:
        return [], "Empty response body", resolved_url

    # ── Extract links using the resolved (post-redirect) URL as base ─
    try:
        links = extract_internal_links(
            html,
            resolved_url,
            include_paths,
            exclude_paths,
            include_subdomains=include_subdomains,
        )
    except Exception as exc:
        return [], f"Link extraction error: {exc}", resolved_url

    return links, None, resolved_url


# ────────────────────────────────────────────────────────────────────────
# Crawl Discovery (Lightweight BFS)
# ────────────────────────────────────────────────────────────────────────


async def discover_crawl(
    seed_url: str,
    max_urls: int,
    max_depth: int,
    include_paths: list[str],
    exclude_paths: list[str],
    include_subdomains: bool,
    ignore_query_params: bool,
    client: httpx.AsyncClient,
    *,
    skip_urls: set[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Lightweight BFS link-following URL discovery.

    Visits pages at each depth level, extracts ``<a href>`` links,
    normalizes and filters them, and enqueues new URLs.  No content
    scraping is performed.

    Parameters
    ----------
    skip_urls : set[str] | None
        URLs to skip (already discovered, e.g. via sitemap). These are
        added to the visited set upfront so the crawler won't revisit them.

    Returns
    -------
    list[dict]
        Each dict has keys: url, path, depth, source, last_modified
    """
    seed_norm = normalize_url(seed_url)
    if not seed_norm:
        logger.error("Invalid seed URL for crawl discovery", extra={"url": seed_url})
        return []

    visited: set[str] = set(skip_urls or [])
    visited.add(seed_norm)

    queue: deque[tuple[str, int]] = deque([(seed_norm, 0)])
    results: list[dict[str, Any]] = []

    semaphore = asyncio.Semaphore(settings.map_max_concurrent)

    logger.info(
        "Crawl discovery started",
        extra={
            "seed_url": seed_norm,
            "max_urls": max_urls,
            "max_depth": max_depth,
            "concurrency": settings.map_max_concurrent,
            "skip_count": len(visited) - 1,
        },
    )

    # ── BFS Loop ───────────────────────────────────────────────────
    while queue and len(results) < max_urls:
        current_depth = queue[0][1]

        # Pop all URLs at current depth level
        batch: list[tuple[str, int]] = []
        while (
            queue
            and queue[0][1] == current_depth
            and len(batch) + len(results) < max_urls
        ):
            batch.append(queue.popleft())

        if not batch:
            continue

        logger.info(
            "Crawl discovery depth batch",
            extra={
                "depth": current_depth,
                "batch_size": len(batch),
                "total_discovered": len(results),
            },
        )

        # ── Inter-depth delay ──────────────────────────────────────
        if current_depth > 0:
            await asyncio.sleep(settings.map_depth_delay)

        # ── Per-page processor ─────────────────────────────────────
        async def _process_one(url: str, depth: int) -> dict[str, Any]:
            async with semaphore:
                if settings.map_delay_ms > 0:
                    await asyncio.sleep(settings.map_delay_ms / 1000.0)

                links, error, resolved_url = await _fetch_and_extract_links(
                    url,
                    client,
                    include_paths,
                    exclude_paths,
                    include_subdomains,
                )

                if error:
                    logger.debug(
                        "Crawl discovery page skipped",
                        extra={"url": url, "depth": depth, "error": error},
                    )

                parsed = urlparse(resolved_url)
                path = parsed.path or "/"

                return {
                    "url": resolved_url,
                    "path": path,
                    "depth": depth,
                    "source": "crawl",
                    "last_modified": None,
                    "_links": links,  # internal — stripped before returning
                    "_error": error,
                }

        # ── Process batch concurrently ─────────────────────────────
        tasks = [_process_one(url, depth) for url, depth in batch]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        # ── Collect results & enqueue children ─────────────────────
        for r in batch_results:
            if isinstance(r, BaseException):
                logger.error(
                    "Crawl discovery batch error",
                    extra={"error": str(r)},
                )
                continue

            links: list[str] = r.pop("_links", [])
            r.pop("_error", None)

            results.append(r)
            # Add resolved URL to visited to prevent re-crawling via redirect chain
            visited.add(r["url"])

            for child_url in links:
                child_norm = normalize_url(child_url, strip_query=ignore_query_params)
                if not child_norm:
                    continue
                if child_norm in visited:
                    continue
                if r["depth"] + 1 > max_depth:
                    continue
                if len(results) + len(queue) >= max_urls:
                    break

                visited.add(child_norm)
                queue.append((child_norm, r["depth"] + 1))

    logger.info(
        "Crawl discovery finished",
        extra={
            "seed_url": seed_norm,
            "total_urls": len(results),
            "max_depth_reached": (max(r["depth"] for r in results) if results else 0),
        },
    )

    return results


# ────────────────────────────────────────────────────────────────────────
# Hybrid Controller
# ────────────────────────────────────────────────────────────────────────


async def discover_hybrid(
    seed_url: str,
    max_urls: int,
    max_depth: int,
    include_paths: list[str],
    exclude_paths: list[str],
    include_subdomains: bool,
    ignore_query_params: bool,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    """
    Hybrid discovery: sitemap first, then crawl to fill remaining capacity.

    Returns a dict with keys:
        urls, sitemap_url, sitemap_count, crawl_count, discovery_method
    """
    seed_norm = normalize_url(seed_url)
    if not seed_norm:
        logger.error("Invalid seed URL for hybrid discovery", extra={"url": seed_url})
        return {
            "urls": [],
            "sitemap_url": None,
            "sitemap_count": 0,
            "crawl_count": 0,
            "discovery_method": "hybrid",
        }

    # ── Step 1: Sitemap ─────────────────────────────────────────────
    sitemap_url = await find_sitemap_url(client, seed_norm)
    sitemap_results: list[dict[str, Any]] = []

    if sitemap_url:
        sitemap_results = await parse_sitemap(
            client,
            sitemap_url,
            include_paths,
            exclude_paths,
            include_subdomains,
            ignore_query_params,
            seed_norm,
        )

    # If sitemap already provides enough URLs, return early
    if len(sitemap_results) >= max_urls:
        logger.info(
            "Hybrid: sitemap sufficient — skipping crawl",
            extra={
                "sitemap_count": len(sitemap_results),
                "max_urls": max_urls,
            },
        )
        return {
            "urls": sitemap_results[:max_urls],
            "sitemap_url": sitemap_url,
            "sitemap_count": len(sitemap_results[:max_urls]),
            "crawl_count": 0,
            "discovery_method": "hybrid",
        }

    # ── Step 2: Crawl to fill gaps ──────────────────────────────────
    remaining = max_urls - len(sitemap_results)
    sitemap_set = {r["url"] for r in sitemap_results}

    logger.info(
        "Hybrid: sitemap done, starting crawl",
        extra={
            "sitemap_count": len(sitemap_results),
            "remaining_slots": remaining,
        },
    )

    crawl_results = await discover_crawl(
        seed_url=seed_norm,
        max_urls=remaining,
        max_depth=max_depth,
        include_paths=include_paths,
        exclude_paths=exclude_paths,
        include_subdomains=include_subdomains,
        ignore_query_params=ignore_query_params,
        client=client,
        skip_urls=sitemap_set,
    )

    combined = sitemap_results + crawl_results

    logger.info(
        "Hybrid discovery finished",
        extra={
            "sitemap_count": len(sitemap_results),
            "crawl_count": len(crawl_results),
            "total": len(combined),
        },
    )

    return {
        "urls": combined[:max_urls],
        "sitemap_url": sitemap_url,
        "sitemap_count": len(sitemap_results),
        "crawl_count": len(crawl_results),
        "discovery_method": "hybrid",
    }


# ────────────────────────────────────────────────────────────────────────
# Unified entry point
# ────────────────────────────────────────────────────────────────────────


async def run_discovery(
    url: str,
    discovery_method: str,
    max_urls: int,
    max_depth: int,
    include_paths: list[str],
    exclude_paths: list[str],
    include_subdomains: bool,
    ignore_query_params: bool,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    """
    Run URL discovery using the specified method.

    Returns a dict with:
        urls, sitemap_url, discovery_method, sitemap_count, crawl_count
    """
    start = time.monotonic()

    seed_norm = normalize_url(url)
    if not seed_norm:
        logger.error("Invalid URL for discovery", extra={"url": url})
        return {
            "urls": [],
            "sitemap_url": None,
            "discovery_method": discovery_method,
            "sitemap_count": 0,
            "crawl_count": 0,
        }

    if discovery_method == "sitemap":
        sitemap_url = await find_sitemap_url(client, seed_norm)
        if not sitemap_url:
            logger.warning(
                "Sitemap not found and method is 'sitemap' — returning empty"
            )
            elapsed = time.monotonic() - start
            return {
                "urls": [],
                "sitemap_url": None,
                "discovery_method": "sitemap",
                "sitemap_count": 0,
                "crawl_count": 0,
            }

        urls = await parse_sitemap(
            client,
            sitemap_url,
            include_paths,
            exclude_paths,
            include_subdomains,
            ignore_query_params,
            seed_norm,
        )
        elapsed = time.monotonic() - start
        logger.info(
            "Sitemap-only discovery finished",
            extra={
                "total_urls": len(urls),
                "duration_ms": int(elapsed * 1000),
            },
        )
        return {
            "urls": urls[:max_urls],
            "sitemap_url": sitemap_url,
            "discovery_method": "sitemap",
            "sitemap_count": min(len(urls), max_urls),
            "crawl_count": 0,
        }

    elif discovery_method == "crawl":
        urls = await discover_crawl(
            seed_url=seed_norm,
            max_urls=max_urls,
            max_depth=max_depth,
            include_paths=include_paths,
            exclude_paths=exclude_paths,
            include_subdomains=include_subdomains,
            ignore_query_params=ignore_query_params,
            client=client,
        )
        elapsed = time.monotonic() - start
        logger.info(
            "Crawl-only discovery finished",
            extra={
                "total_urls": len(urls),
                "duration_ms": int(elapsed * 1000),
            },
        )
        return {
            "urls": urls[:max_urls],
            "sitemap_url": None,
            "discovery_method": "crawl",
            "sitemap_count": 0,
            "crawl_count": len(urls),
        }

    else:  # hybrid
        result = await discover_hybrid(
            seed_url=seed_norm,
            max_urls=max_urls,
            max_depth=max_depth,
            include_paths=include_paths,
            exclude_paths=exclude_paths,
            include_subdomains=include_subdomains,
            ignore_query_params=ignore_query_params,
            client=client,
        )
        elapsed = time.monotonic() - start
        logger.info(
            "Hybrid discovery finished",
            extra={
                "total_urls": len(result["urls"]),
                "sitemap_count": result["sitemap_count"],
                "crawl_count": result["crawl_count"],
                "duration_ms": int(elapsed * 1000),
            },
        )
        result["discovery_method"] = result.get("discovery_method", "hybrid")
        return result
