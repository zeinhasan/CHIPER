"""
API Route Handlers

Defines the /api/v1/research endpoint and registers it with FastAPI.
Instrumented with Prometheus metrics for observability.
"""

import time

from fastapi import APIRouter, Request

from app.models.schemas import ResearchRequest, ResearchResponse, ScrapeResult
from app.services import scraper, searxng, summarizer
from app.utils.helpers import get_logger
from app.utils.metrics import (
    searxng_requests,
    searxng_results,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["research"])


@router.post("/research", response_model=ResearchResponse)
async def research(request: Request, payload: ResearchRequest) -> ResearchResponse:
    """
    Execute a full research pipeline:

    1. Query SearXNG for URLs matching the search query.
    2. Scrape each URL using the Two-Tier strategy (httpx → Playwright).
    3. Optionally generate an AI summary of all scraped content via DeepSeek.
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

    # ── Step 1: SearXNG ────────────────────────────────────────────
    try:
        search_results = await searxng.search(
            payload.query, max_results=payload.max_results
        )
    except RuntimeError as exc:
        logger.error("SearXNG stage failed", extra={"error": str(exc)})
        return ResearchResponse(
            query=payload.query,
            ai_summary=None,
            results=[],
            total_results=0,
        )

    if not search_results:
        logger.info("No search results from SearXNG", extra={"query": payload.query})
        return ResearchResponse(
            query=payload.query,
            ai_summary="Tidak ada hasil pencarian dari SearXNG.",
            results=[],
            total_results=0,
        )

    urls = [r["url"] for r in search_results if r.get("url")]

    logger.info(
        "Starting scraping phase",
        extra={"query": payload.query, "url_count": len(urls)},
    )

    # ── Step 2: Two-Tier Scraping ────────────────────────────────────
    browser = request.app.state.playwright_browser

    scrape_results = await scraper.scrape_urls(
        urls=urls,
        browser=browser,
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

    # ── Step 3: AI Summarization (optional) ─────────────────────────
    ai_summary: str | None = None

    if payload.generate_summary:
        documents = [r.markdown_content for r in results if r.markdown_content]
        if documents:
            try:
                ai_summary = await summarizer.summarize(documents, payload.query)
            except RuntimeError as exc:
                logger.warning("Summarization skipped", extra={"error": str(exc)})
                ai_summary = f"⚠️ Gagal membuat ringkasan: {exc}"
        else:
            ai_summary = "Tidak ada konten yang berhasil diekstrak untuk diringkas."

    logger.info(
        "Research complete",
        extra={
            "query": payload.query,
            "total_results": len(results),
            "success_count": success_count,
            "has_summary": ai_summary is not None,
        },
    )

    return ResearchResponse(
        query=payload.query,
        ai_summary=ai_summary,
        results=results,
        total_results=len(results),
    )
