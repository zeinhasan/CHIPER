"""
API Route Handlers

Defines /api/v1/research and /api/v1/research/{task_id} endpoints.
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
    ResearchRequest,
    ResearchResponse,
    ScrapeResult,
    TaskStatusResponse,
)
from app.services import scraper, searxng, summarizer
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
