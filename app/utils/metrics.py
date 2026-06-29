"""
Prometheus Metrics Definitions

All business-level metrics for CHIPER observability.
Exposed via /metrics endpoint.
"""

from prometheus_client import REGISTRY, Counter, Histogram, generate_latest
from prometheus_fastapi_instrumentator import Instrumentator

# ── SearXNG Metrics ─────────────────────────────────────────────────

searxng_duration = Histogram(
    "chiper_searxng_duration_seconds",
    "Duration of SearXNG API calls in seconds.",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

searxng_requests = Counter(
    "chiper_searxng_requests_total",
    "Total SearXNG requests.",
    ["status"],  # "success" | "error"
)

searxng_results = Counter(
    "chiper_searxng_results_total",
    "Total search results returned by SearXNG.",
)

# ── Scraping Metrics ─────────────────────────────────────────────────

scrape_duration = Histogram(
    "chiper_scrape_duration_seconds",
    "Duration of individual URL scraping operations.",
    ["fetch_method"],  # "httpx" | "playwright" | "playwright-failed"
    buckets=(0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 60.0),
)

scrape_total = Counter(
    "chiper_scrape_total",
    "Total scraping attempts.",
    ["fetch_method", "status"],  # status: "success" | "failure"
)

tier_fallback = Counter(
    "chiper_tier_fallback_total",
    "Total fallbacks from Tier-1 (httpx) to Tier-2 (Playwright).",
)

# ── Summarization Metrics ────────────────────────────────────────────

summarize_duration = Histogram(
    "chiper_summarize_duration_seconds",
    "Duration of AI summarization calls.",
    buckets=(1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 60.0),
)

summarize_total = Counter(
    "chiper_summarize_total",
    "Total AI summarization attempts.",
    ["status"],  # "success" | "error"
)

# ── FastAPI HTTP Metrics Instrumentator ──────────────────────────────

instrumentator = Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    should_instrument_requests_inprogress=True,
    excluded_handlers=["/health", "/metrics"],
    inprogress_name="chiper_http_requests_inprogress",
    inprogress_labels=True,
)
