"""
In-Memory Task Store

Stores background task results for polling via GET /api/v1/research/{task_id}.
Tasks auto-expire after TTL seconds.
Supports optional max-inflight-task limiting to prevent memory exhaustion.
"""

import time
import uuid
from typing import Any

from app.config import settings
from app.utils.helpers import get_logger

logger = get_logger(__name__)


class TaskStore:
    """Thread-safe in-memory store for async task results."""

    def __init__(self, ttl: int = 600):
        self._tasks: dict[str, dict] = {}
        self._ttl = ttl  # seconds before auto-cleanup
        self._last_cleanup: float = 0.0
        self._cleanup_interval: float = 60.0  # seconds between periodic cleanups
        self._cleanup_threshold: int = 100  # force cleanup if tasks exceed this

    @property
    def inflight(self) -> int:
        """Number of currently-running (processing) tasks."""
        return sum(1 for t in self._tasks.values() if t["status"] == "processing")

    @property
    def max_inflight(self) -> int:
        """Maximum allowed concurrent tasks (0 = unlimited)."""
        return settings.max_inflight_tasks

    def create(self, query: str | None = None) -> str:
        """
        Create a new task entry and return its ID.

        Raises ``RuntimeError`` when the max-inflight limit has been reached.
        """
        # ── Enforce concurrency limit ───────────────────────────
        if self.max_inflight > 0 and self.inflight >= self.max_inflight:
            logger.warning(
                "Task rejected — max inflight reached",
                extra={
                    "inflight": self.inflight,
                    "max": self.max_inflight,
                },
            )
            raise RuntimeError(
                f"Too many concurrent tasks ({self.inflight}/{self.max_inflight}). "
                "Please try again later."
            )

        task_id = str(uuid.uuid4())
        self._tasks[task_id] = {
            "status": "processing",
            "result": None,
            "query": query,
            "created_at": time.monotonic(),
        }
        self._cleanup_if_needed()
        return task_id

    def complete(self, task_id: str, result: Any) -> None:
        """Mark a task as completed with its result."""
        if task_id in self._tasks:
            self._tasks[task_id]["status"] = "done"
            self._tasks[task_id]["result"] = result

    def fail(self, task_id: str, error: str) -> None:
        """Mark a task as failed with an error message."""
        if task_id in self._tasks:
            self._tasks[task_id]["status"] = "error"
            self._tasks[task_id]["result"] = error

    def get(self, task_id: str) -> dict | None:
        """Retrieve a task by ID. Returns None if not found."""
        self._cleanup_if_needed()
        return self._tasks.get(task_id)

    def cleanup_expired(self) -> int:
        """Remove expired tasks. Returns count of removed tasks."""
        now = time.monotonic()
        expired = [
            tid for tid, t in self._tasks.items() if now - t["created_at"] > self._ttl
        ]
        for tid in expired:
            del self._tasks[tid]
        self._last_cleanup = now
        return len(expired)

    def _cleanup_if_needed(self) -> None:
        """Run cleanup only if enough time has passed or task count is high."""
        now = time.monotonic()
        time_elapsed = now - self._last_cleanup
        if (
            time_elapsed >= self._cleanup_interval
            or len(self._tasks) >= self._cleanup_threshold
        ):
            self.cleanup_expired()


# Singleton instance
task_store = TaskStore(ttl=600)  # 10 minutes TTL
