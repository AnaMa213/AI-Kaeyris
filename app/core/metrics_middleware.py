"""HTTP metrics middleware (Jalon 6 — Observability §Phase 2).

Captures per-request latency and counts in
:data:`app.core.metrics.HTTP_REQUESTS_TOTAL` and
:data:`app.core.metrics.HTTP_REQUEST_DURATION_SECONDS`.

Important: we label by the **route template** (e.g.
``/services/jdr/sessions/{session_id}/artifacts/summary``), not the
concrete path with UUIDs — otherwise the cardinality of metric series
would explode as 1 series per session UUID, defeating the purpose.

Starlette exposes the matched route via ``request.scope['route'].path``
after the routing layer has run. The middleware reads it post-call.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.metrics import HTTP_REQUEST_DURATION_SECONDS, HTTP_REQUESTS_TOTAL


class MetricsMiddleware(BaseHTTPMiddleware):
    """Record HTTP latency + counts per (method, route template, status)."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start

        # Resolve the matched route template (post-routing). Falls back to
        # the raw path if routing didn't match (404 on unknown URL).
        route = request.scope.get("route")
        route_path = getattr(route, "path", None) or request.url.path

        HTTP_REQUEST_DURATION_SECONDS.labels(
            method=request.method, route=route_path
        ).observe(duration)
        HTTP_REQUESTS_TOTAL.labels(
            method=request.method,
            route=route_path,
            status=str(response.status_code),
        ).inc()

        return response
