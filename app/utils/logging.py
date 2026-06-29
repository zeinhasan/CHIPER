"""
Structured JSON Logging with Trace Context

Configures the root logger to output JSON-formatted logs.
Uses contextvars to inject X-Request-ID into every log entry.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

from app.config import settings

# Context variable: stores the current request's trace ID.
# Set by the tracing middleware, read by the log formatter.
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")


class JSONFormatter(logging.Formatter):
    """JSON log formatter with trace_id injection."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Inject trace ID if available
        trace_id = trace_id_var.get("")
        if trace_id:
            log_entry["trace_id"] = trace_id

        # Include exception info if present
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = str(record.exc_info[1])

        # Include extra fields passed via `extra=...`
        for key in ("url", "duration_ms", "fetch_method", "content_length", "query"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val

        return json.dumps(log_entry, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable console formatter (used when LOG_FORMAT=console)."""

    def format(self, record: logging.LogRecord) -> str:
        trace_id = trace_id_var.get("")
        prefix = f"[{trace_id[:8]}] " if trace_id else ""
        return (
            f"{self.formatTime(record)} [{record.levelname}] "
            f"{prefix}{record.name}: {record.getMessage()}"
        )


def setup_logging() -> None:
    """Configure the root CHIPER logger with the chosen format."""

    logger = logging.getLogger("chiper")
    logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)

    if settings.log_format == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(ConsoleFormatter())

    logger.addHandler(handler)

    # Suppress noisy third-party loggers
    for noisy in ("httpx", "httpcore", "openai", "trafilatura", "markdownify"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str = "chiper") -> logging.Logger:
    """Get a logger instance for the given module name."""
    return logging.getLogger(name)
