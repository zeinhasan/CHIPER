"""Tests for app.utils.task_store (in-memory task store + inflight limit — NB-2)."""

import pytest

from app.config import settings
from app.utils.task_store import TaskStore


def test_create_and_get():
    store = TaskStore(ttl=600)
    tid = store.create(query="hello")
    task = store.get(tid)
    assert task is not None
    assert task["status"] == "processing"
    assert task["query"] == "hello"


def test_complete():
    store = TaskStore(ttl=600)
    tid = store.create()
    store.complete(tid, {"answer": 42})
    task = store.get(tid)
    assert task["status"] == "done"
    assert task["result"] == {"answer": 42}


def test_fail():
    store = TaskStore(ttl=600)
    tid = store.create()
    store.fail(tid, "boom")
    task = store.get(tid)
    assert task["status"] == "error"
    assert task["result"] == "boom"


def test_get_unknown_returns_none():
    store = TaskStore(ttl=600)
    assert store.get("does-not-exist") is None


def test_inflight_counts_only_processing():
    store = TaskStore(ttl=600)
    a = store.create()
    store.create()
    store.complete(a, "x")
    assert store.inflight == 1


def test_inflight_limit_enforced(monkeypatch):
    monkeypatch.setattr(settings, "max_inflight_tasks", 2)
    store = TaskStore(ttl=600)
    store.create()
    store.create()
    with pytest.raises(RuntimeError):
        store.create()  # third exceeds the limit of 2


def test_inflight_limit_zero_means_unlimited(monkeypatch):
    monkeypatch.setattr(settings, "max_inflight_tasks", 0)
    store = TaskStore(ttl=600)
    for _ in range(50):
        store.create()
    assert store.inflight == 50


def test_cleanup_expired():
    store = TaskStore(ttl=600)
    tid = store.create()
    # Age the entry past its TTL, then run cleanup.
    store._tasks[tid]["created_at"] -= 10_000
    removed = store.cleanup_expired()
    assert removed >= 1
    assert store.get(tid) is None


def test_cleanup_keeps_fresh_entries():
    store = TaskStore(ttl=600)
    tid = store.create()
    assert store.cleanup_expired() == 0
    assert store.get(tid) is not None
