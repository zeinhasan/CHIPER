"""
API Route Handlers

Defines /api/v1/research, /api/v1/research/{task_id},
/api/v1/crawl, /api/v1/crawl/{task_id},
/api/v1/map, and /api/v1/map/{task_id} endpoints.
Protected by circuit breaker (SearXNG) and rate limiting (slowapi).
Supports both synchronous and async (background) execution modes via run_async flag.
Distributes scraping across browser pool (round-robin).
Instrumented with Prometheus metrics for observability.
"""

import asyncio

from fastapi import APIRouter, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings
from app.models.schemas import (
    CrawlPage,
    CrawlRequest,
    CrawlResponse,
    CrawlTaskStatusResponse,
    MapRequest,
    MapResponse,
    MapTaskStatusResponse,
    MapUrl,
    ResearchRequest,
    ResearchResponse,
    ScrapeResult,
    TaskStatusResponse,
)
from app.services import crawler, discovery, scraper, searxng, summarizer
from app.utils.circuit_breaker import CircuitBreakerOpenError
from app.utils.helpers import get_logger
from app.utils.task_store import task_store

logger = get_logger(__name__)

# Domains that are video/image/media platforms and not scrapable for text research.
# These are filtered out before scraping to avoid wasting resources.
BLOCKED_DOMAINS: set[str] = {
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "m.youtube.com",
    "vimeo.com",
    "www.vimeo.com",
    "dailymotion.com",
    "www.dailymotion.com",
    "tiktok.com",
    "www.tiktok.com",
    "instagram.com",
    "www.instagram.com",
    "twitch.tv",
    "www.twitch.tv",
    "bilibili.com",
    "www.bilibili.com",
    "rumble.com",
    "www.rumble.com",
    "odysee.com",
    "www.odysee.com",
}


def _is_blocked_domain(url: str) -> bool:
    """Check if a URL is from a blocked video/media domain."""
    from urllib.parse import urlparse

    try:
        hostname = urlparse(url).hostname or ""
        return hostname.lower() in BLOCKED_DOMAINS
    except Exception:
        return False


router = APIRouter(prefix="/api/v1", tags=["research"])
limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit])


@router.post("/research", response_model=ResearchResponse)
@limiter.limit(settings.rate_limit)
async def research(request: Request, payload: ResearchRequest) -> ResearchResponse:
    """
    Execute a full research pipeline.

    Two modes:
    - Synchronous (run_async=False, default): All steps run immediately.
      If generate_summary=True, AI summarization also runs synchronously
      and the summary is returned directly in the response.
    - Async (run_async=True): The entire pipeline (search + scrape + summary)
      runs as a background task. Returns a task_id immediately.
      Use GET /api/v1/research/{task_id} to poll for the full result.
    """
    logger.info(
        "Research request received",
        extra={
            "query": payload.query,
            "max_results": payload.max_results,
            "force_js": payload.force_js_render,
            "summary": payload.generate_summary,
            "run_async": payload.run_async,
        },
    )

    # ── Async Mode: run entire pipeline in background ──────────────
    if payload.run_async:
        task_id = task_store.create(payload.query)
        logger.info(
            "Starting background full-research pipeline",
            extra={"task_id": task_id, "query": payload.query},
        )
        asyncio.create_task(
            _run_full_research(
                task_id=task_id,
                payload=payload,
                http_client=request.app.state.http_client,
                browsers=request.app.state.playwright_browsers,
                searxng_circuit=request.app.state.searxng_circuit,
            )
        )
        return ResearchResponse(
            query=payload.query,
            task_id=task_id,
            results=[],
            total_results=0,
        )

    # ── Sync Mode ───────────────────────────────────────────────────
    # Shared resources from app state
    http_client = request.app.state.http_client
    browsers = request.app.state.playwright_browsers
    searxng_circuit = request.app.state.searxng_circuit

    # ── Step 1: SearXNG (with circuit breaker) ────────────────────
    try:
        async with searxng_circuit:
            search_results = await searxng.search(
                query=payload.query,
                client=http_client,
                max_results=payload.max_results,
            )
            searxng_circuit.success()
    except CircuitBreakerOpenError as exc:
        logger.warning("SearXNG circuit breaker open: %s", exc)
        return ResearchResponse(
            query=payload.query,
            results=[],
            total_results=0,
        )
    except RuntimeError as exc:
        logger.error("SearXNG stage failed: %s", exc)
        return ResearchResponse(
            query=payload.query,
            results=[],
            total_results=0,
        )

    if not search_results:
        logger.info("No search results from SearXNG", extra={"query": payload.query})
        return ResearchResponse(
            query=payload.query,
            results=[],
            total_results=0,
        )

    urls = [r["url"] for r in search_results if r.get("url")]

    # Filter out video/media domains that can't be scraped for text
    filtered_urls = [u for u in urls if not _is_blocked_domain(u)]
    if len(filtered_urls) < len(urls):
        logger.info(
            "Filtered out blocked domains",
            extra={"before": len(urls), "after": len(filtered_urls)},
        )

    logger.info(
        "Starting scraping phase",
        extra={"query": payload.query, "url_count": len(filtered_urls)},
    )

    # ── Step 2: Two-Tier Scraping (browser pool, round-robin) ─────
    scrape_results = await scraper.scrape_urls(
        urls=filtered_urls,
        browsers=browsers,
        client=http_client,
        force_js_render=payload.force_js_render,
        max_concurrent_playwright=3,
    )

    # Merge SearXNG titles with scrape results
    search_by_url = {r["url"]: r for r in search_results}
    results: list[ScrapeResult] = []
    for sr in scrape_results:
        meta = search_by_url.get(sr["url"], {})
        results.append(
            ScrapeResult(
                url=sr["url"],
                title=meta.get("title"),
                fetch_method=sr["fetch_method"],
                markdown_content=sr["markdown_content"],
                content_length=sr["content_length"],
            )
        )

    success_count = sum(1 for r in results if r.content_length > 0)

    # ── Step 3: AI Summarization (sync in this mode) ───────────
    if payload.generate_summary:
        documents = [r.markdown_content for r in results if r.markdown_content]
        if documents:
            logger.info(
                "Running summarization synchronously",
                extra={"document_count": len(documents)},
            )
            try:
                ai_summary = await summarizer.summarize(documents, payload.query)
            except Exception as exc:
                logger.error("Summarization failed", extra={"error": str(exc)})
                ai_summary = f"Summarization failed: {exc}"

            logger.info(
                "Research complete (with summary)",
                extra={
                    "query": payload.query,
                    "total_results": len(results),
                    "success_count": success_count,
                },
            )

            return ResearchResponse(
                query=payload.query,
                ai_summary=ai_summary,
                results=results,
                total_results=len(results),
            )

    logger.info(
        "Research complete",
        extra={
            "query": payload.query,
            "total_results": len(results),
            "success_count": success_count,
        },
    )

    return ResearchResponse(
        query=payload.query,
        results=results,
        total_results=len(results),
    )


async def _run_full_research(
    task_id: str,
    payload: ResearchRequest,
    http_client,
    browsers,
    searxng_circuit,
) -> None:
    """Background task: run the entire research pipeline and store full result."""
    try:
        # ── Step 1: SearXNG (with circuit breaker) ────────────────
        try:
            async with searxng_circuit:
                search_results = await searxng.search(
                    query=payload.query,
                    client=http_client,
                    max_results=payload.max_results,
                )
                searxng_circuit.success()
        except CircuitBreakerOpenError as exc:
            logger.warning("SearXNG circuit breaker open: %s", exc)
            task_store.complete(
                task_id,
                {"query": payload.query, "results": [], "total_results": 0},
            )
            return
        except RuntimeError as exc:
            logger.error("SearXNG stage failed: %s", exc)
            task_store.fail(task_id, str(exc))
            return

        if not search_results:
            logger.info(
                "No search results from SearXNG", extra={"query": payload.query}
            )
            task_store.complete(
                task_id,
                {"query": payload.query, "results": [], "total_results": 0},
            )
            return

        urls = [r["url"] for r in search_results if r.get("url")]

        # Filter out video/media domains that can't be scraped for text
        filtered_urls = [u for u in urls if not _is_blocked_domain(u)]
        if len(filtered_urls) < len(urls):
            logger.info(
                "Filtered out blocked domains (background)",
                extra={"before": len(urls), "after": len(filtered_urls)},
            )

        logger.info(
            "Starting scraping phase (background)",
            extra={"query": payload.query, "url_count": len(filtered_urls)},
        )

        # ── Step 2: Two-Tier Scraping ─────────────────────────────
        scrape_results = await scraper.scrape_urls(
            urls=filtered_urls,
            browsers=browsers,
            client=http_client,
            force_js_render=payload.force_js_render,
            max_concurrent_playwright=3,
        )

        search_by_url = {r["url"]: r for r in search_results}
        results = []
        for sr in scrape_results:
            meta = search_by_url.get(sr["url"], {})
            results.append(
                {
                    "url": sr["url"],
                    "title": meta.get("title"),
                    "fetch_method": sr["fetch_method"],
                    "markdown_content": sr["markdown_content"],
                    "content_length": sr["content_length"],
                }
            )

        # ── Step 3: AI Summarization (always run in async mode) ────
        ai_summary = None
        documents = [r["markdown_content"] for r in results if r["markdown_content"]]
        if documents:
            try:
                ai_summary = await summarizer.summarize(documents, payload.query)
            except Exception as exc:
                logger.error(
                    "Summarization failed in background",
                    extra={"task_id": task_id, "error": str(exc)},
                )
                ai_summary = f"Summarization failed: {exc}"

        success_count = sum(1 for r in results if r["content_length"] > 0)
        logger.info(
            "Background research complete",
            extra={
                "task_id": task_id,
                "query": payload.query,
                "total_results": len(results),
                "success_count": success_count,
            },
        )

        task_store.complete(
            task_id,
            {
                "query": payload.query,
                "ai_summary": ai_summary,
                "results": results,
                "total_results": len(results),
            },
        )

    except Exception as exc:
        error_msg = str(exc)
        task_store.fail(task_id, error_msg)
        logger.error(
            "Background full-research failed",
            extra={"task_id": task_id, "error": error_msg},
        )


@router.get("/research/{task_id}")
async def get_task_status(task_id: str):
    """
    Poll for the result of a background full-research task (run_async=true).

    Returns:
        - status="processing": task is still running
        - status="done": result is ready (query, results, total_results, ai_summary populated)
        - status="error": task failed (ai_summary contains error message)
        - status="not_found": task_id not found or expired
    """
    task = task_store.get(task_id)

    if task is None:
        return TaskStatusResponse(
            task_id=task_id,
            status="not_found",
        )

    if task["status"] == "done":
        result = task["result"]
        if isinstance(result, dict):
            raw_results = result.get("results", [])
            scraped = [
                ScrapeResult(
                    url=r["url"],
                    title=r.get("title"),
                    fetch_method=r["fetch_method"],
                    markdown_content=r["markdown_content"],
                    content_length=r.get("content_length", 0),
                )
                for r in raw_results
            ]
            return TaskStatusResponse(
                task_id=task_id,
                status="done",
                query=result.get("query"),
                ai_summary=result.get("ai_summary"),
                results=scraped,
                total_results=result.get("total_results", 0),
            )

    if task["status"] == "error":
        return TaskStatusResponse(
            task_id=task_id,
            status="error",
            ai_summary=task["result"],
        )

    return TaskStatusResponse(
        task_id=task_id,
        status=task["status"],
        query=task.get("query"),
    )


# ── Crawl ────────────────────────────────────────────────────────────────


@router.post("/crawl", response_model=CrawlResponse)
@limiter.limit(settings.crawl_rate_limit)
async def crawl_endpoint(request: Request, payload: CrawlRequest) -> CrawlResponse:
    """
    Recursively crawl a website, following internal links and extracting Markdown.

    Supports two modes:
    - Synchronous (run_async=False, default): Crawl runs immediately, returns full results.
    - Async (run_async=True): Crawl runs as background task.
      Use GET /api/v1/crawl/{task_id} to poll for progress and results.
    """
    logger.info(
        "Crawl request received",
        extra={
            "url": payload.url,
            "max_depth": payload.max_depth,
            "max_pages": payload.max_pages,
            "force_js": payload.force_js_render,
            "summary": payload.generate_summary,
            "run_async": payload.run_async,
        },
    )

    # ── Async Mode ────────────────────────────────────────────────
    if payload.run_async:
        task_id = task_store.create(payload.url)
        logger.info(
            "Starting background crawl", extra={"task_id": task_id, "url": payload.url}
        )
        asyncio.create_task(
            _run_crawl_background(
                task_id=task_id,
                payload=payload,
                http_client=request.app.state.http_client,
                browsers=request.app.state.playwright_browsers,
            )
        )
        return CrawlResponse(
            base_url=payload.url, total_pages=0, task_id=task_id, results=[]
        )

    # ── Sync Mode ─────────────────────────────────────────────────
    http_client = request.app.state.http_client
    browsers = request.app.state.playwright_browsers

    crawl_results = await crawler.crawl(
        seed_url=payload.url,
        max_depth=payload.max_depth,
        max_pages=payload.max_pages,
        include_paths=payload.include_paths,
        exclude_paths=payload.exclude_paths,
        force_js_render=payload.force_js_render,
        http_client=http_client,
        browsers=browsers,
    )

    # ── Optional AI Summarization ──────────────────────────────────
    ai_summary: str | None = None
    if payload.generate_summary:
        documents = [
            r["markdown_content"] for r in crawl_results if r.get("markdown_content")
        ]
        if documents:
            logger.info(
                "Running crawl summarization",
                extra={"document_count": len(documents)},
            )
            try:
                ai_summary = await summarizer.summarize(documents, payload.url)
            except Exception as exc:
                logger.error("Crawl summarization failed", extra={"error": str(exc)})
                ai_summary = f"Summarization failed: {exc}"

    pages = [
        CrawlPage(
            url=r["url"],
            title=r.get("title"),
            depth=r.get("depth", 0),
            fetch_method=r.get("fetch_method", "unknown"),
            markdown_content=r.get("markdown_content", ""),
            content_length=r.get("content_length", 0),
            links_found=r.get("links_found", 0),
        )
        for r in crawl_results
    ]

    logger.info(
        "Crawl complete",
        extra={
            "url": payload.url,
            "total_pages": len(pages),
        },
    )

    return CrawlResponse(
        base_url=payload.url,
        total_pages=len(pages),
        results=pages,
        ai_summary=ai_summary,
    )


async def _run_crawl_background(
    task_id: str,
    payload: CrawlRequest,
    http_client,
    browsers,
) -> None:
    """Background task: execute full crawl pipeline and store result in task_store."""
    try:
        crawl_results = await crawler.crawl(
            seed_url=payload.url,
            max_depth=payload.max_depth,
            max_pages=payload.max_pages,
            include_paths=payload.include_paths,
            exclude_paths=payload.exclude_paths,
            force_js_render=payload.force_js_render,
            http_client=http_client,
            browsers=browsers,
        )

        ai_summary: str | None = None
        if payload.generate_summary:
            documents = [
                r["markdown_content"]
                for r in crawl_results
                if r.get("markdown_content")
            ]
            if documents:
                try:
                    ai_summary = await summarizer.summarize(documents, payload.url)
                except Exception as exc:
                    ai_summary = f"Summarization failed: {exc}"

        pages = [
            {
                "url": r["url"],
                "title": r.get("title"),
                "depth": r.get("depth", 0),
                "fetch_method": r.get("fetch_method", "unknown"),
                "markdown_content": r.get("markdown_content", ""),
                "content_length": r.get("content_length", 0),
                "links_found": r.get("links_found", 0),
            }
            for r in crawl_results
        ]

        task_store.complete(
            task_id,
            {
                "base_url": payload.url,
                "total_pages": len(pages),
                "ai_summary": ai_summary,
                "results": pages,
            },
        )

        logger.info(
            "Background crawl complete",
            extra={"task_id": task_id, "total_pages": len(pages)},
        )

    except Exception as exc:
        error_msg = str(exc)
        task_store.fail(task_id, error_msg)
        logger.error(
            "Background crawl failed",
            extra={"task_id": task_id, "error": error_msg},
        )


@router.get("/crawl/{task_id}")
async def get_crawl_task_status(task_id: str) -> CrawlTaskStatusResponse:
    """
    Poll for the result of a background crawl task (run_async=true).

    Returns:
        - status="processing": task is still running
        - status="done": result is ready (base_url, total_pages, results, ai_summary)
        - status="error": task failed (ai_summary contains error message)
        - status="not_found": task_id not found or expired
    """
    task = task_store.get(task_id)

    if task is None:
        return CrawlTaskStatusResponse(task_id=task_id, status="not_found")

    if task["status"] == "done":
        result = task["result"]
        if isinstance(result, dict):
            raw_results = result.get("results", [])
            pages = [CrawlPage(**r) for r in raw_results]
            return CrawlTaskStatusResponse(
                task_id=task_id,
                status="done",
                base_url=result.get("base_url"),
                total_pages=result.get("total_pages"),
                ai_summary=result.get("ai_summary"),
                results=pages,
            )

    if task["status"] == "error":
        return CrawlTaskStatusResponse(
            task_id=task_id,
            status="error",
            ai_summary=task["result"],  # error message stored in result
        )

    return CrawlTaskStatusResponse(task_id=task_id, status=task["status"])


# ── Site Map Discovery ───────────────────────────────────────────────────


@router.post("/map", response_model=MapResponse)
@limiter.limit(settings.crawl_rate_limit)
async def map_endpoint(request: Request, payload: MapRequest) -> MapResponse:
    """
    Discover all URLs on a website.

    Supports three discovery methods:
    - sitemap: Parse sitemap.xml only (fast, relies on site's sitemap).
    - crawl: Lightweight BFS link-follower (no content scraping).
    - hybrid: Sitemap first, then crawl to fill gaps (default).

    Supports async mode via run_async=True — poll with GET /api/v1/map/{task_id}.
    """
    logger.info(
        "Map request received",
        extra={
            "url": payload.url,
            "method": payload.discovery_method,
            "max_urls": payload.max_urls,
            "max_depth": payload.max_depth,
            "include_subdomains": payload.include_subdomains,
        },
    )

    # ── Async Mode ────────────────────────────────────────────────
    if payload.run_async:
        task_id = task_store.create(payload.url)
        logger.info(
            "Starting background map discovery",
            extra={"task_id": task_id, "url": payload.url},
        )
        asyncio.create_task(
            _run_map_background(
                task_id=task_id,
                payload=payload,
                http_client=request.app.state.http_client,
            )
        )
        return MapResponse(
            base_url=payload.url,
            total_urls=0,
            urls=[],
            task_id=task_id,
            discovery_method=payload.discovery_method,
        )

    # ── Sync Mode ─────────────────────────────────────────────────
    client = request.app.state.http_client
    result = await discovery.run_discovery(
        url=payload.url,
        discovery_method=payload.discovery_method,
        max_urls=payload.max_urls,
        max_depth=payload.max_depth,
        include_paths=payload.include_paths,
        exclude_paths=payload.exclude_paths,
        include_subdomains=payload.include_subdomains,
        ignore_query_params=payload.ignore_query_params,
        client=client,
    )

    urls = [MapUrl(**u) for u in result["urls"]]

    logger.info(
        "Map discovery complete",
        extra={
            "url": payload.url,
            "total_urls": len(urls),
        },
    )

    return MapResponse(
        base_url=payload.url,
        total_urls=len(urls),
        urls=urls,
        sitemap_url=result.get("sitemap_url"),
        discovery_method=result["discovery_method"],
        sitemap_count=result.get("sitemap_count", 0),
        crawl_count=result.get("crawl_count", 0),
    )


async def _run_map_background(
    task_id: str,
    payload: MapRequest,
    http_client,
) -> None:
    """Background task: execute URL discovery and store result in task_store."""
    try:
        result = await discovery.run_discovery(
            url=payload.url,
            discovery_method=payload.discovery_method,
            max_urls=payload.max_urls,
            max_depth=payload.max_depth,
            include_paths=payload.include_paths,
            exclude_paths=payload.exclude_paths,
            include_subdomains=payload.include_subdomains,
            ignore_query_params=payload.ignore_query_params,
            client=http_client,
        )

        task_store.complete(
            task_id,
            {
                "base_url": payload.url,
                "total_urls": len(result["urls"]),
                "urls": result["urls"],
                "sitemap_url": result.get("sitemap_url"),
                "discovery_method": result["discovery_method"],
                "sitemap_count": result.get("sitemap_count", 0),
                "crawl_count": result.get("crawl_count", 0),
            },
        )

        logger.info(
            "Background map discovery complete",
            extra={"task_id": task_id, "total_urls": len(result["urls"])},
        )

    except Exception as exc:
        error_msg = str(exc)
        task_store.fail(task_id, error_msg)
        logger.error(
            "Background map discovery failed",
            extra={"task_id": task_id, "error": error_msg},
        )


@router.get("/map/{task_id}")
async def get_map_task_status(task_id: str) -> MapTaskStatusResponse:
    """
    Poll for the result of a background map discovery task (run_async=true).

    Returns:
        - status="processing": task is still running
        - status="done": result is ready (base_url, total_urls, urls, etc.)
        - status="error": task failed (base_url contains error message)
        - status="not_found": task_id not found or expired
    """
    task = task_store.get(task_id)

    if task is None:
        return MapTaskStatusResponse(task_id=task_id, status="not_found")

    if task["status"] == "done":
        result = task["result"]
        if isinstance(result, dict):
            raw_urls = result.get("urls", [])
            urls = [MapUrl(**u) for u in raw_urls]
            return MapTaskStatusResponse(
                task_id=task_id,
                status="done",
                base_url=result.get("base_url"),
                total_urls=result.get("total_urls"),
                urls=urls,
                sitemap_url=result.get("sitemap_url"),
                discovery_method=result.get("discovery_method"),
                sitemap_count=result.get("sitemap_count"),
                crawl_count=result.get("crawl_count"),
            )

    if task["status"] == "error":
        return MapTaskStatusResponse(
            task_id=task_id,
            status="error",
            base_url=task["result"],  # error message stored in result
        )

    return MapTaskStatusResponse(task_id=task_id, status=task["status"])
