"""Tests for the structured logging setup (Jalon 6 — Phase 1).

We don't assert on prod log output (would couple to a specific renderer
chain). We assert that:
- ``configure_logging`` is idempotent (safe to call twice in tests + main)
- ``get_logger(name)`` returns a logger that surfaces the module name
- ``contextvars.bind_contextvars`` propagates to subsequent log records
- the request middleware echoes / mints an ``X-Request-Id`` header
"""

import json

import structlog
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.logging import configure_logging, get_logger
from app.core.request_context import REQUEST_ID_HEADER, RequestContextMiddleware


def test_configure_logging_is_idempotent():
    """Calling configure_logging twice doesn't crash."""
    configure_logging(json_mode=False)
    configure_logging(json_mode=True)
    configure_logging(json_mode=False)


def test_get_logger_returns_bound_logger():
    configure_logging(json_mode=False)
    logger = get_logger("test.module")
    # Smoke: emitting a log shouldn't raise.
    logger.info("test.event", key="value")


def test_contextvars_propagate_to_subsequent_logs(capsys):
    """Logs emitted after bind_contextvars carry the bound key."""
    configure_logging(json_mode=True)
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="abc-123")

    logger = get_logger("test.ctx")
    logger.info("test.event")

    captured = capsys.readouterr()
    # JSON renderer writes to stderr (configured in core/logging.py)
    line = captured.err.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "test.event"
    assert payload["request_id"] == "abc-123"

    structlog.contextvars.clear_contextvars()


# ---------------------------------------------------------------------------
# RequestContextMiddleware
# ---------------------------------------------------------------------------


def _make_minimal_app() -> FastAPI:
    """Tiny app to test the middleware in isolation."""
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/_test")
    async def echo():
        return {"ok": True}

    return app


async def test_request_middleware_mints_request_id_when_absent():
    app = _make_minimal_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_test")
    assert response.status_code == 200
    rid = response.headers.get(REQUEST_ID_HEADER)
    assert rid is not None
    assert len(rid) >= 16  # UUIDv4 hex = 32 chars


async def test_request_middleware_trusts_incoming_request_id():
    """If the client sends X-Request-Id, the middleware echoes it back."""
    app = _make_minimal_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/_test", headers={REQUEST_ID_HEADER: "client-supplied-id-42"}
        )
    assert response.status_code == 200
    assert response.headers.get(REQUEST_ID_HEADER) == "client-supplied-id-42"


async def test_request_middleware_isolates_concurrent_requests():
    """Two requests in a row should have distinct request_ids."""
    app = _make_minimal_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.get("/_test")
        r2 = await client.get("/_test")
    assert r1.headers[REQUEST_ID_HEADER] != r2.headers[REQUEST_ID_HEADER]
