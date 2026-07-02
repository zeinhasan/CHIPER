"""
Pydantic models for CHIPER API request/response validation.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


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
    error: str | None = None
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

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("URL cannot be empty")
        v = v.strip()
        if "://" not in v:
            v = "https://" + v
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        if len(v) > 2048:
            raise ValueError("URL too long (max 2048 characters)")
        return v

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
    error: str | None = None
    results: list[CrawlPage] | None = None


# ── Site Map Discovery ───────────────────────────────────────────────


class MapRequest(BaseModel):
    """Incoming site map discovery payload."""

    url: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="Base URL to discover.",
    )

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("URL cannot be empty")
        v = v.strip()
        if "://" not in v:
            v = "https://" + v
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        if len(v) > 2048:
            raise ValueError("URL too long (max 2048 characters)")
        return v

    discovery_method: Literal["sitemap", "crawl", "hybrid"] = Field(
        default="hybrid",
        description="Method: 'sitemap' (parse sitemap.xml only), "
        "'crawl' (link-following only), 'hybrid' (sitemap then crawl).",
    )
    max_urls: int = Field(
        default=500,
        ge=1,
        le=5000,
        description="Maximum URLs to return.",
    )
    max_depth: int = Field(
        default=10,
        ge=1,
        le=20,
        description="Maximum crawl depth (crawl/hybrid only).",
    )
    include_paths: list[str] = Field(
        default_factory=list,
        description="Regex patterns to include (whitelist).",
    )
    exclude_paths: list[str] = Field(
        default_factory=list,
        description="Regex patterns to exclude (blacklist).",
    )
    include_subdomains: bool = Field(
        default=False,
        description="If True, treat subdomains as internal.",
    )
    ignore_query_params: bool = Field(
        default=False,
        description="If True, strip query params before dedup.",
    )
    run_async: bool = Field(
        default=False,
        description="If True, run as background task. Use GET /api/v1/map/{task_id} to poll.",
    )


class MapUrl(BaseModel):
    """A single discovered URL entry."""

    url: str
    path: str
    depth: int
    source: str  # "sitemap" or "crawl"
    last_modified: str | None = None


class MapResponse(BaseModel):
    """API response for a site map discovery request."""

    base_url: str
    total_urls: int
    urls: list[MapUrl]
    sitemap_url: str | None = None
    discovery_method: str
    sitemap_count: int = 0
    crawl_count: int = 0
    task_id: str | None = None


class MapTaskStatusResponse(BaseModel):
    """API response for polling a background map discovery task."""

    task_id: str
    status: str  # "processing", "done", "error", "not_found"
    base_url: str | None = None
    total_urls: int | None = None
    error: str | None = None
    urls: list[MapUrl] | None = None
    sitemap_url: str | None = None
    discovery_method: str | None = None
    sitemap_count: int | None = None
    crawl_count: int | None = None


# ── Structured Data Extraction ─────────────────────────────────────


class ExtractRequest(BaseModel):
    """Incoming structured data extraction payload."""

    urls: list[str] = Field(
        ...,
        min_length=1,
        max_length=20,
        description="List of URLs to extract data from (1-20).",
    )

    @field_validator("urls")
    @classmethod
    def _validate_urls(cls, v: list[str]) -> list[str]:
        for i, url in enumerate(v):
            if not url or not url.strip():
                raise ValueError(f"URL at index {i} cannot be empty")
            url = url.strip()
            if "://" not in url:
                url = "https://" + url
            if not url.startswith(("http://", "https://")):
                raise ValueError(
                    f"URL at index {i} must start with http:// or https://"
                )
            if len(url) > 2048:
                raise ValueError(f"URL at index {i} too long (max 2048 characters)")
            v[i] = url
        return v

    prompt: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description="Natural-language description of the data to extract.",
    )
    json_schema: dict | None = Field(
        default=None,
        description="Optional JSON Schema to validate the extracted data.",
    )
    extract_mode: Literal["combined", "per_page"] = Field(
        default="combined",
        description="'combined' = one extraction from combined content; "
        "'per_page' = extract each URL independently.",
    )
    force_js_render: bool = Field(
        default=False,
        description="If True, use Playwright for all pages (skip Tier-1).",
    )
    run_async: bool = Field(
        default=False,
        description="If True, run as background task. Use GET /api/v1/extract/{task_id} to poll.",
    )


class ExtractData(BaseModel):
    """A single extraction result for one URL."""

    url: str
    extraction: dict | list | str | None = None
    error: str | None = None


class ExtractResponse(BaseModel):
    """API response for an extraction request."""

    success: bool
    data: list[ExtractData]
    total_urls: int
    failed_urls: int = 0
    extract_mode: str
    task_id: str | None = None


class ExtractTaskStatusResponse(BaseModel):
    """API response for polling a background extraction task."""

    task_id: str
    status: str  # "processing", "done", "error", "not_found"
    success: bool | None = None
    data: list[ExtractData] | None = None
    total_urls: int | None = None
    failed_urls: int | None = None
    extract_mode: str | None = None
    error: str | None = None
