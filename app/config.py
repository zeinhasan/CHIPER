"""
CHIPER Configuration Module

Reads settings from environment variables with sensible defaults.
"""

import os


class Settings:
    """Application settings loaded from environment variables."""

    # --- SearXNG ---
    searxng_base_url: str = os.getenv("SEARXNG_BASE_URL", "http://localhost:8080")
    searxng_categories: str = os.getenv("SEARXNG_CATEGORIES", "web,news")

    # --- AI Summarization (OpenAI-compatible) ---
    cmd_api_key: str = os.getenv("CMD_API_KEY", "")
    cmd_base_url: str = os.getenv("CMD_BASE_URL", "https://openrouter.ai/api/v1")
    cmd_model: str = os.getenv("CMD_MODEL", "deepseek/deepseek-chat")

    # --- Playwright ---
    playwright_browser_path: str | None = os.getenv("PLAYWRIGHT_BROWSER_PATH") or None
    browser_pool_size: int = int(os.getenv("BROWSER_POOL_SIZE", "2"))

    # --- Server ---
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))

    # --- Scraping ---
    min_content_length: int = int(os.getenv("MIN_CONTENT_LENGTH", "200"))

    # --- PDF Scraping ---
    pdf_enabled: bool = os.getenv("PDF_ENABLED", "true").lower() in ("1", "true", "yes")
    pdf_max_pages: int = int(os.getenv("PDF_MAX_PAGES", "50"))

    # --- Observability ---
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_format: str = os.getenv("LOG_FORMAT", "json")  # "json" or "console"

    # --- Reliability: Retry ---
    fetch_static_retries: int = int(os.getenv("FETCH_STATIC_RETRIES", "3"))
    fetch_dynamic_retries: int = int(os.getenv("FETCH_DYNAMIC_RETRIES", "2"))
    fetch_retry_base_delay: float = float(os.getenv("FETCH_RETRY_BASE_DELAY", "1.0"))

    # --- Reliability: Circuit Breaker ---
    circuit_breaker_failures: int = int(os.getenv("CIRCUIT_BREAKER_FAILURES", "5"))
    circuit_breaker_timeout: float = float(os.getenv("CIRCUIT_BREAKER_TIMEOUT", "30.0"))

    # --- Reliability: Rate Limiting ---
    rate_limit: str = os.getenv("RATE_LIMIT", "30/minute")
    crawl_rate_limit: str = os.getenv("CRAWL_RATE_LIMIT", "5/minute")

    # --- Crawl ---
    crawl_max_concurrent: int = int(os.getenv("CRAWL_MAX_CONCURRENT", "5"))
    crawl_delay_ms: int = int(os.getenv("CRAWL_DELAY_MS", "500"))
    crawl_depth_delay: float = float(os.getenv("CRAWL_DEPTH_DELAY", "1.0"))
    crawl_playwright_concurrency: int = int(
        os.getenv("CRAWL_PLAYWRIGHT_CONCURRENCY", "3")
    )

    # --- Map Discovery ---
    map_max_concurrent: int = int(os.getenv("MAP_MAX_CONCURRENT", "10"))
    map_delay_ms: int = int(os.getenv("MAP_DELAY_MS", "200"))
    map_depth_delay: float = float(os.getenv("MAP_DEPTH_DELAY", "0.5"))

    # --- Extraction ---
    extract_max_urls: int = int(os.getenv("EXTRACT_MAX_URLS", "20"))
    extract_max_content_chars: int = int(os.getenv("EXTRACT_MAX_CONTENT_CHARS", "8000"))
    extract_temperature: float = float(os.getenv("EXTRACT_TEMPERATURE", "0.1"))
    extract_retries: int = int(os.getenv("EXTRACT_RETRIES", "1"))

    # --- Domain Filtering ---
    # Comma-separated global allow/block lists (empty allowlist = allow all).
    domain_allowlist: set[str] = {
        d.strip().lower()
        for d in os.getenv("DOMAIN_ALLOWLIST", "").split(",")
        if d.strip()
    }
    domain_blocklist: set[str] = {
        d.strip().lower()
        for d in os.getenv("DOMAIN_BLOCKLIST", "").split(",")
        if d.strip()
    }

    # --- Security ---
    chiper_api_key: str = os.getenv("CHIPER_API_KEY", "")
    chiper_cors_origins: str = os.getenv(
        "CHIPER_CORS_ORIGINS", ""
    )  # comma-separated, empty = no CORS
    scrape_max_body_mb: int = int(os.getenv("SCRAPE_MAX_BODY_MB", "50"))
    max_inflight_tasks: int = int(os.getenv("MAX_INFLIGHT_TASKS", "0"))  # 0 = unlimited
    crawl_total_timeout: float = float(
        os.getenv("CRAWL_TOTAL_TIMEOUT", "300.0")
    )  # 5 min
    map_total_timeout: float = float(os.getenv("MAP_TOTAL_TIMEOUT", "120.0"))  # 2 min
    extract_total_timeout: float = float(
        os.getenv("EXTRACT_TOTAL_TIMEOUT", "300.0")
    )  # 5 min
    research_total_timeout: float = float(
        os.getenv("RESEARCH_TOTAL_TIMEOUT", "300.0")
    )  # 5 min

    # --- Persistent History ---
    history_enabled: bool = os.getenv("HISTORY_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    database_url: str = os.getenv(
        "DATABASE_URL", "sqlite+aiosqlite:///./chiper_history.db"
    )
    history_retention_days: int = int(os.getenv("HISTORY_RETENTION_DAYS", "30"))
    history_store_full_content: bool = os.getenv(
        "HISTORY_STORE_FULL_CONTENT", "false"
    ).lower() in ("1", "true", "yes")


# Singleton instance
settings = Settings()
