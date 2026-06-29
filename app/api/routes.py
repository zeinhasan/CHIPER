"""
API Route Handlers

Defines /api/v1/research and /api/v1/research/{task_id} endpoints.
Protected by circuit breaker (SearXNG) and rate limiting (slowapi).
Uses background tasks for AI summarization to keep response fast.
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

router = APIRouter(prefix="/api/v1", tags=["research"])
limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit])


@router.post("/research", response_model=ResearchResponse)
@limiter.limit(settings.rate_limit)
async def research(request: Request, payload: ResearchRequest) -> ResearchResponse:
    """
    Execute a full research pipeline.

    Steps 1-2 (SearXNG + Scraping) run synchronously and return immediately.
    Step 3 (AI Summarization) runs in the background if generate_summary=True.
    Use GET /api/v1/research/{task_id} to poll for the summary result.
    """
    logger.info(
        "Research request received",
        extra={
            "query": payload.query,
            "max_results": payload.max_results,
            "force_js": payload.force_js_render,
            "summary": payload.generate_summary,
        },
    )

    # Shared resources from app state
    http_client = request.app.state.http_client
    browser = request.app.state.playwright_browser
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

    logger.info(
        "Starting scraping phase",
        extra={"query": payload.query, "url_count": len(urls)},
    )

    # ── Step 2: Two-Tier Scraping (synchronous) ────────────────────
    scrape_results = await scraper.scrape_urls(
        urls=urls,
        browser=browser,
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

    # ── Step 3: AI Summarization (background) ──────────────────────
    if payload.generate_summary:
        documents = [r.markdown_content for r in results if r.markdown_content]
        if documents:
            task_id = task_store.create()
            logger.info(
                "Starting background summarization",
                extra={"task_id": task_id, "document_count": len(documents)},
            )

            # Fire-and-forget: summarization runs in background
            asyncio.create_task(_run_summarization(task_id, documents, payload.query))

            # Return immediately with scraped results + task_id
            return ResearchResponse(
                query=payload.query,
                task_id=task_id,
                ai_summary=None,
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


async def _run_summarization(task_id: str, documents: list[str], query: str) -> None:
    """Background task: run AI summarization and store result in task store."""
    try:
        summary = await summarizer.summarize(documents, query)
        task_store.complete(task_id, summary)
        logger.info("Background summarization done", extra={"task_id": task_id})
    except Exception as exc:
        error_msg = str(exc)
        task_store.fail(task_id, error_msg)
        logger.error(
            "Background summarization failed",
            extra={"task_id": task_id, "error": error_msg},
        )


@router.get("/research/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str) -> TaskStatusResponse:
    """
    Poll for the result of a background summarization task.

    Returns:
        - status="processing": summary is still being generated
        - status="done": summary is ready (ai_summary field populated)
        - status="error": summarization failed (ai_summary contains error message)
    """
    task = task_store.get(task_id)

    if task is None:
        return TaskStatusResponse(
            task_id=task_id,
            status="not_found",
        )

    return TaskStatusResponse(
        task_id=task_id,
        status=task["status"],
        ai_summary=task["result"] if task["status"] in ("done", "error") else None,
    )
