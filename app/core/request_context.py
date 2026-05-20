"""Per-request context middleware (Jalon 6 — Observability §Phase 1).

Binds a unique ``request_id`` (UUIDv4) to every HTTP request via
``structlog.contextvars``. Every log emitted while the request is alive
gets the field automatically — no plumbing required at log sites.

If the client sends an ``X-Request-Id`` header, we trust it (useful for
distributed tracing in front of a reverse proxy). Otherwise we generate
one. The same value is echoed back in the response header so the client
can correlate API calls with server logs.

Sources:
- structlog contextvars: https://www.structlog.org/en/stable/contextvars.html
- Starlette BaseHTTPMiddleware: https://www.starlette.io/middleware/
"""

from __future__ import annotations

import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

REQUEST_ID_HEADER = "X-Request-Id"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind a request_id contextvar for the duration of every HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        # Trust an incoming X-Request-Id if present (e.g. from a proxy),
        # otherwise mint a fresh UUIDv4.
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex

        # Clear any leftovers from a previous request on the same task,
        # then bind. structlog.contextvars uses ContextVar under the hood
        # so isolation between concurrent requests is preserved.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
