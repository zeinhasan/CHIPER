"""Tests for app.middleware.auth (API key authentication — NB-5).

Skips if starlette/fastapi test client stack is unavailable.
"""

import pytest

pytest.importorskip("starlette")
from starlette.applications import Starlette  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.middleware.auth import ApiKeyMiddleware  # noqa: E402


def _make_client() -> TestClient:
    async def ok(request):
        return JSONResponse({"ok": True})

    async def health(request):
        return JSONResponse({"status": "ok"})

    app = Starlette(
        routes=[
            Route("/api/v1/research", ok, methods=["GET"]),
            Route("/health", health, methods=["GET"]),
        ]
    )
    app.add_middleware(ApiKeyMiddleware)
    return TestClient(app)


def test_auth_disabled_when_no_key(monkeypatch):
    monkeypatch.setattr(settings, "chiper_api_key", "")
    client = _make_client()
    assert client.get("/api/v1/research").status_code == 200


def test_missing_key_returns_401(monkeypatch):
    monkeypatch.setattr(settings, "chiper_api_key", "secret123")
    client = _make_client()
    resp = client.get("/api/v1/research")
    assert resp.status_code == 401


def test_wrong_key_returns_403(monkeypatch):
    monkeypatch.setattr(settings, "chiper_api_key", "secret123")
    client = _make_client()
    resp = client.get("/api/v1/research", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 403


def test_correct_key_allows(monkeypatch):
    monkeypatch.setattr(settings, "chiper_api_key", "secret123")
    client = _make_client()
    resp = client.get("/api/v1/research", headers={"X-API-Key": "secret123"})
    assert resp.status_code == 200


def test_public_health_exempt(monkeypatch):
    monkeypatch.setattr(settings, "chiper_api_key", "secret123")
    client = _make_client()
    assert client.get("/health").status_code == 200
