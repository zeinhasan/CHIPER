"""Tests for app.services.history (persistent history — feature 19).

Uses an in-memory SQLite DB. Skips if aiosqlite is not installed.
"""

import pytest

pytest.importorskip("aiosqlite")

from app.config import settings  # noqa: E402
from app.services import history  # noqa: E402


@pytest.fixture
def mem_db(monkeypatch, run_async):
    """Initialize an in-memory history DB for a single test, then dispose."""
    monkeypatch.setattr(settings, "history_enabled", True)
    monkeypatch.setattr(settings, "database_url", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(settings, "history_store_full_content", True)
    monkeypatch.setattr(settings, "history_retention_days", 30)
    run_async(history.init_db())
    yield
    run_async(history.close())


def test_save_and_get(mem_db, run_async):
    async def _flow():
        await history.save(
            id="task-1",
            kind="research",
            query_or_url="hello",
            params={"max_results": 5},
            status="done",
            result={"total_results": 3},
            result_size=3,
        )
        return await history.get("task-1")

    entry = run_async(_flow())
    assert entry is not None
    assert entry["kind"] == "research"
    assert entry["result_size"] == 3
    assert entry["params"] == {"max_results": 5}
    assert entry["result"] == {"total_results": 3}


def test_get_missing_returns_none(mem_db, run_async):
    assert run_async(history.get("nope")) is None


def test_list_filters_by_kind(mem_db, run_async):
    async def _flow():
        for i in range(3):
            await history.save(
                id=f"c{i}",
                kind="crawl",
                query_or_url=f"https://s{i}.com",
                params=None,
                status="done",
                result={"total_pages": i},
                result_size=i,
            )
        await history.save(
            id="m0",
            kind="map",
            query_or_url="https://x.com",
            params=None,
            status="done",
            result={"total_urls": 9},
            result_size=9,
        )
        crawls = await history.list_history(kind="crawl", limit=50)
        maps = await history.list_history(kind="map", limit=50)
        all_entries = await history.list_history(limit=50)
        return crawls, maps, all_entries

    crawls, maps, all_entries = run_async(_flow())
    assert len(crawls) == 3
    assert len(maps) == 1
    assert len(all_entries) == 4


def test_list_pagination(mem_db, run_async):
    async def _flow():
        for i in range(5):
            await history.save(
                id=f"e{i}",
                kind="extract",
                query_or_url="https://x.com",
                params=None,
                status="done",
                result={},
                result_size=0,
            )
        return await history.list_history(limit=2, offset=0)

    page = run_async(_flow())
    assert len(page) == 2


def test_save_is_best_effort_when_disabled(monkeypatch, run_async):
    # When the engine is not initialized, save must not raise.
    monkeypatch.setattr(history, "_engine", None)

    async def _flow():
        await history.save(
            id=None,
            kind="research",
            query_or_url="q",
            params=None,
            status="done",
            result=None,
            result_size=0,
        )
        return await history.get("anything")

    assert run_async(_flow()) is None


def test_summary_only_storage(monkeypatch, run_async):
    """When history_store_full_content is False, only summary keys are kept."""
    monkeypatch.setattr(settings, "history_enabled", True)
    monkeypatch.setattr(settings, "database_url", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(settings, "history_store_full_content", False)

    async def _flow():
        await history.init_db()
        await history.save(
            id="x1",
            kind="research",
            query_or_url="q",
            params=None,
            status="done",
            result={"ai_summary": "S", "results": [1, 2, 3], "total_results": 3},
            result_size=3,
        )
        entry = await history.get("x1")
        await history.close()
        return entry

    entry = run_async(_flow())
    # "results" (bulky) dropped; summary keys retained
    assert "results" not in entry["result"]
    assert entry["result"]["ai_summary"] == "S"
    assert entry["result"]["total_results"] == 3
