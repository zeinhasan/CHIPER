"""
Shared pytest fixtures & path setup for CHIPER tests.

Some tests exercise async functions; since pytest-asyncio may not be installed,
we provide a tiny `run_async` helper that wraps `asyncio.run`.
"""

import asyncio
import os
import sys

import pytest

# Make the project root importable (so `import app...` works).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def run_async():
    """Return a helper that runs a coroutine to completion."""

    def _run(coro):
        return asyncio.run(coro)

    return _run
