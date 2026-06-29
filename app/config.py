"""
CHIPER Configuration Module

Reads settings from environment variables with sensible defaults.
"""

import os


class Settings:
    """Application settings loaded from environment variables."""

    # --- SearXNG ---
    searxng_base_url: str = os.getenv("SEARXNG_BASE_URL", "http://localhost:8080")

    # --- AI Summarization (CommandCode / DeepSeek) ---
    cmd_api_key: str = os.getenv("CMD_API_KEY", "")
    cmd_base_url: str = os.getenv(
        "CMD_BASE_URL", "https://api.commandcode.ai/provider/v1"
    )
    cmd_model: str = os.getenv("CMD_MODEL", "deepseek/deepseek-v4-pro")

    # --- Playwright ---
    playwright_browser_path: str | None = os.getenv("PLAYWRIGHT_BROWSER_PATH") or None

    # --- Server ---
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))

    # --- Scraping ---
    min_content_length: int = int(os.getenv("MIN_CONTENT_LENGTH", "200"))

    # --- Observability ---
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_format: str = os.getenv("LOG_FORMAT", "json")  # "json" or "console"


# Singleton instance
settings = Settings()
