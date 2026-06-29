"""
Simple Circuit Breaker

Prevents cascading failures by temporarily stopping requests
to a service after a threshold of consecutive failures.
"""

import threading
import time
from enum import Enum


class CircuitState(Enum):
    CLOSED = "closed"  # Normal operation - requests pass through
    OPEN = "open"  # Circuit tripped - requests fail fast
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitBreaker:
    """
    A thread-safe circuit breaker implementation.

    Usage:
        cb = CircuitBreaker(name="searxng", failure_threshold=5, recovery_timeout=30)

        async with cb:
            # call the protected service
            result = await service.call()
            cb.success()
    """

    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._transition()
            return self._state

    def success(self) -> None:
        """Report a successful call, resetting the circuit."""
        with self._lock:
            self._failure_count = 0
            self._state = CircuitState.CLOSED

    def failure(self) -> None:
        """Report a failed call."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN

    def allow_request(self) -> bool:
        """Check if a request is allowed through the circuit."""
        with self._lock:
            self._transition()
            return self._state != CircuitState.OPEN

    def _transition(self) -> None:
        """Internal state machine - check if circuit should recover."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN

    async def __aenter__(self):
        if not self.allow_request():
            raise CircuitBreakerOpenError(
                f"Circuit '{self.name}' is OPEN. "
                f"Try again in {self.recovery_timeout:.0f}s."
            )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.failure()
        return False  # Don't suppress exceptions


class CircuitBreakerOpenError(Exception):
    """Raised when a request is rejected because the circuit is open."""

    pass
