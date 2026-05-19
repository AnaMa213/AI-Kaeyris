"""Tests for the Prometheus metrics layer (Jalon 6 — Phase 2).

Smoke tests on the exposition format and the presence of the expected
metric names. Detailed counter values are not asserted: prometheus_client
maintains module-level state that persists across tests in the default
REGISTRY, making exact-value assertions brittle. We verify behaviour:
the endpoint serves, the names exist, increments happen.
"""

from collections.abc import Callable
from typing import Any

import fakeredis
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.metrics_middleware import MetricsMiddleware
from app.core.redis_client import get_redis
from app.services.jdr.router import router as jdr_router


def _make_jdr_app(make_db_session_dep: Callable[..., Any]) -> FastAPI:
    """Build a FastAPI app with the metrics middleware + jdr router for tests."""
    app = FastAPI()
    app.add_middleware(MetricsMiddleware)
    register_exception_handlers(app)
    app.include_router(jdr_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = lambda: fakeredis.FakeStrictRedis()
    # Add a /metrics endpoint locally for the test
    from fastapi.responses import Response
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    @app.get("/metrics", include_in_schema=False)
    def _metrics():
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


# ---------------------------------------------------------------------------
# /metrics endpoint
# ---------------------------------------------------------------------------


async def test_metrics_endpoint_returns_prometheus_text(
    db_session, make_db_session_dep
):
    """GET /metrics returns text/plain with the Prometheus exposition format."""
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/metrics")

    assert response.status_code == 200
    ct = response.headers.get("content-type", "")
    assert "text/plain" in ct
    # version=... charset=... in the content type (Prometheus convention)
    assert "version=" in ct
    body = response.text
    # Each metric definition starts with HELP/TYPE comments
    assert "# HELP" in body
    assert "# TYPE" in body


async def test_metrics_endpoint_exposes_expected_metric_names(
    db_session, make_db_session_dep
):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/metrics")

    body = response.text
    # Application-level metrics declared in app.core.metrics
    expected_names = (
        "kaeyris_http_requests_total",
        "kaeyris_http_request_duration_seconds",
        "kaeyris_llm_calls_total",
        "kaeyris_llm_tokens_total",
        "kaeyris_llm_call_duration_seconds",
        "kaeyris_transcription_calls_total",
        "kaeyris_transcription_duration_seconds",
        "kaeyris_jobs_total",
        "kaeyris_job_duration_seconds",
    )
    for name in expected_names:
        assert name in body, f"Missing metric in /metrics output: {name}"


# ---------------------------------------------------------------------------
# Middleware behaviour: HTTP requests increment the counters
# ---------------------------------------------------------------------------


async def test_http_request_increments_metrics(
    db_session, make_db_session_dep
):
    """A real request through the middleware bumps the HTTP counter."""
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Hit a known route — the unauthenticated GET /sessions returns 401,
        # which is fine: we only check the metric got recorded with a status.
        await client.get("/services/jdr/sessions")
        scrape = await client.get("/metrics")

    body = scrape.text
    # The series for that route+method+401 should now appear
    assert 'kaeyris_http_requests_total{' in body
    # And contain at least one sample for /services/jdr/sessions
    assert "/services/jdr/sessions" in body
