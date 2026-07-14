"""Tests for app.utils.circuit_breaker."""

import time

import pytest

from app.utils.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
)


def test_starts_closed():
    cb = CircuitBreaker(name="t", failure_threshold=3, recovery_timeout=1.0)
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request()


def test_opens_after_threshold():
    cb = CircuitBreaker(name="t", failure_threshold=3, recovery_timeout=10.0)
    for _ in range(3):
        cb.failure()
    assert cb.state == CircuitState.OPEN
    assert not cb.allow_request()


def test_success_resets():
    cb = CircuitBreaker(name="t", failure_threshold=2, recovery_timeout=10.0)
    cb.failure()
    cb.success()
    cb.failure()  # only 1 failure since reset
    assert cb.state == CircuitState.CLOSED


def test_half_open_after_timeout():
    cb = CircuitBreaker(name="t", failure_threshold=1, recovery_timeout=0.1)
    cb.failure()
    assert cb.state == CircuitState.OPEN
    time.sleep(0.15)
    # accessing state triggers the transition to HALF_OPEN
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.allow_request()


def test_async_context_manager_raises_when_open(run_async):
    cb = CircuitBreaker(name="t", failure_threshold=1, recovery_timeout=10.0)
    cb.failure()

    async def _use():
        async with cb:
            pass

    with pytest.raises(CircuitBreakerOpenError):
        run_async(_use())


def test_async_context_manager_records_failure(run_async):
    cb = CircuitBreaker(name="t", failure_threshold=1, recovery_timeout=10.0)

    async def _use():
        async with cb:
            raise ValueError("boom")

    with pytest.raises(ValueError):
        run_async(_use())
    # the failure inside the context should have opened the circuit
    assert cb.state == CircuitState.OPEN
