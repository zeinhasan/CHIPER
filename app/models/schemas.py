"""
Pydantic models for CHIPER API request/response validation.
"""

from pydantic import BaseModel, Field


class ResearchRequest(BaseModel):
    """Incoming research query payload."""

    query: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="The search query / research question.",
        examples=["Laporan keuangan terbaru perusahaan teknologi"],
    )
    max_results: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of search results to fetch and scrape.",
    )
    force_js_render: bool = Field(
        default=False,
        description="If True, skip Tier-1 (static) and go straight to Playwright for all URLs.",
    )
    generate_summary: bool = Field(
        default=False,
        description="If True, aggregate all scraped content and generate an AI summary via DeepSeek.",
    )
    run_async: bool = Field(
        default=False,
        description="If True, run the entire research pipeline as a background task. Use GET /api/v1/research/{task_id} to poll for results. When enabled, summarization is always triggered regardless of generate_summary.",
    )


class ScrapeResult(BaseModel):
    """A single scraped result."""

    url: str
    title: str | None = None
    fetch_method: str  # "httpx" or "playwright"
    markdown_content: str
    content_length: int = 0


class ResearchResponse(BaseModel):
    """API response for a research query."""

    query: str
    task_id: str | None = None
    ai_summary: str | None = None
    results: list[ScrapeResult]
    total_results: int


class TaskStatusResponse(BaseModel):
    """API response for polling a background task (summarization or full research)."""

    task_id: str
    status: str  # "processing", "done", "error", "not_found"
    query: str | None = None
    ai_summary: str | None = None
    results: list[ScrapeResult] | None = None
    total_results: int | None = None


# ── Crawl ─────────────────────────────────────────────────────────────


class CrawlRequest(BaseModel):
    """Incoming crawl payload."""

    url: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="Starting URL to crawl.",
    )
    max_depth: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum crawl depth from the base URL.",
    )
    max_pages: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum total pages to crawl.",
    )
    include_paths: list[str] = Field(
        default_factory=list,
        description="Regex patterns to include (whitelist).",
    )
    exclude_paths: list[str] = Field(
        default_factory=list,
        description="Regex patterns to exclude (blacklist).",
    )
    force_js_render: bool = Field(
        default=False,
        description="If True, use Playwright for all pages (skip Tier-1).",
    )
    generate_summary: bool = Field(
        default=False,
        description="If True, generate AI summary of all crawled content.",
    )
    run_async: bool = Field(
        default=False,
        description="If True, run crawl as background task. Use GET /api/v1/crawl/{task_id} to poll.",
    )


class CrawlPage(ScrapeResult):
    """Extends ScrapeResult with crawl-specific fields."""

    depth: int = Field(..., description="Depth level from seed URL (0 = seed).")
    links_found: int = Field(
        default=0, description="Number of internal links found on this page."
    )


class CrawlResponse(BaseModel):
    """API response for a crawl request."""

    base_url: str
    total_pages: int
    results: list[CrawlPage]
    task_id: str | None = None
    ai_summary: str | None = None


class CrawlTaskStatusResponse(BaseModel):
    """API response for polling a background crawl task."""

    task_id: str
    status: str  # "processing", "done", "error", "not_found"
    base_url: str | None = None
    total_pages: int | None = None
    ai_summary: str | None = None
    results: list[CrawlPage] | None = None
