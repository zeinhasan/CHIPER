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
    """API response for polling a background summarization task."""

    task_id: str
    status: str  # "processing", "done", "error"
    query: str | None = None
    ai_summary: str | None = None
