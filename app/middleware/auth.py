"""
API Key Authentication Middleware

Validates ``X-API-Key`` header against ``CHIPER_API_KEY`` environment variable.
Public endpoints (``/health``, ``/metrics``) are exempt from authentication.

When ``CHIPER_API_KEY`` is empty (default), authentication is disabled entirely,
which is suitable for development or when running behind a reverse proxy
that handles auth.
"""

import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings

# Paths that do NOT require authentication.
_PUBLIC_PATHS: frozenset[str] = frozenset({"/health", "/metrics"})

HEADER_API_KEY = "X-API-Key"


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """
    Require a valid API key for every request unless the endpoint is public
    or the server has not been configured with a key (dev mode).
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # ── Public endpoints ──────────────────────────────────────
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        # ── Auth disabled (no key configured) ─────────────────────
        if not settings.chiper_api_key:
            return await call_next(request)

        # ── Validate API key ──────────────────────────────────────
        client_key = request.headers.get(HEADER_API_KEY)
        if not client_key:
            return JSONResponse(
                status_code=401,
                content={
                    "detail": (
                        "Missing API key.  Provide it via the "
                        f"'{HEADER_API_KEY}' header."
                    )
                },
            )

        # Constant-time comparison to avoid leaking the key via timing.
        if not secrets.compare_digest(client_key, settings.chiper_api_key):
            return JSONResponse(
                status_code=403,
                content={"detail": "Invalid API key."},
            )

        # Key is valid — proceed
        return await call_next(request)
