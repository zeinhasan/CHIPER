"""
Request Tracing Middleware

Injects X-Request-ID into every HTTP request/response cycle.
- Reads existing X-Request-ID header, or generates a new UUID.
- Sets trace_id context variable (used by structured logger).
- Returns X-Request-ID in the response header.
"""

import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.utils.logging import trace_id_var

HEADER_REQUEST_ID = "X-Request-ID"


class TracingMiddleware(BaseHTTPMiddleware):
    """
    Middleware that ensures every request has a traceable request ID.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Read existing or generate new
        trace_id = request.headers.get(HEADER_REQUEST_ID) or str(uuid.uuid4())

        # Store in context so logger and downstream code can access it
        token = trace_id_var.set(trace_id)

        try:
            response: Response = await call_next(request)
            response.headers[HEADER_REQUEST_ID] = trace_id
            return response
        finally:
            # Reset context to prevent leaking between requests
            trace_id_var.reset(token)
