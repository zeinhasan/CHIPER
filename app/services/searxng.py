"""
SearXNG Integration Service

Communicates with a self-hosted SearXNG instance to retrieve search results.
Protected by a circuit breaker and instrumented with Prometheus metrics.
"""

import time

import httpx

from app.config import settings
from app.utils.helpers import get_logger
from app.utils.metrics import searxng_duration, searxng_requests, searxng_results

logger = get_logger(__name__)


async def search(
    query: str,
    client: httpx.AsyncClient,
    max_results: int = 5,
) -> list[dict]:
    """
    Query the SearXNG JSON API and return a list of result dicts.

    Uses a shared httpx.AsyncClient for connection pool reuse.

    Each result dict contains:
        - url: str
        - title: str
        - content: str (snippet)
    """

    searxng_url = f"{settings.searxng_base_url}/search"
    params = {
        "q": query,
        "format": "json",
        "categories": "general",
        "pageno": 1,
    }

    logger.info("Querying SearXNG", extra={"query": query, "max_results": max_results})
    start = time.monotonic()

    try:
        response = await client.get(searxng_url, params=params)
        response.raise_for_status()
        data = response.json()
        searxng_requests.labels(status="success").inc()
    except httpx.HTTPError as exc:
        searxng_requests.labels(status="error").inc()
        logger.error("SearXNG request failed", extra={"query": query})
        raise RuntimeError(f"Failed to reach SearXNG: {exc}") from exc

    elapsed = time.monotonic() - start
    searxng_duration.observe(elapsed)

    results = data.get("results", [])
    searxng_results.inc(len(results))

    if not results:
        logger.warning("SearXNG returned 0 results", extra={"query": query})
        return []

    limited = results[:max_results]

    logger.info(
        "SearXNG search complete",
        extra={
            "query": query,
            "total_results": len(results),
            "limited_results": len(limited),
            "duration_ms": int(elapsed * 1000),
        },
    )

    return [
        {
            "url": r.get("url", ""),
            "title": r.get("title", ""),
            "content": r.get("content", ""),
        }
        for r in limited
    ]
