"""
In-Memory Task Store

Stores background task results for polling via GET /api/v1/research/{task_id}.
Tasks auto-expire after TTL seconds.
"""

import time
import uuid
from typing import Any


class TaskStore:
    """Thread-safe in-memory store for async task results."""

    def __init__(self, ttl: int = 600):
        self._tasks: dict[str, dict] = {}
        self._ttl = ttl  # seconds before auto-cleanup

    def create(self, query: str | None = None) -> str:
        """Create a new task entry and return its ID."""
        task_id = str(uuid.uuid4())
        self._tasks[task_id] = {
            "status": "processing",
            "result": None,
            "query": query,
            "created_at": time.monotonic(),
        }
        self._cleanup()
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
        self._cleanup()
        return self._tasks.get(task_id)

    def _cleanup(self) -> None:
        """Remove expired tasks."""
        now = time.monotonic()
        expired = [
            tid for tid, t in self._tasks.items() if now - t["created_at"] > self._ttl
        ]
        for tid in expired:
            del self._tasks[tid]


# Singleton instance
task_store = TaskStore(ttl=600)  # 10 minutes TTL
