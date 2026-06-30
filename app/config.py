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


# Singleton instance
settings = Settings()
