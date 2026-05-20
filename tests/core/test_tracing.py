"""Tests for the OpenTelemetry tracing setup (Jalon 6 — Phase 4).

We don't run a real OTLP collector in tests, and we don't activate the
real instrumentations either: ``FastAPIInstrumentor`` / ``SQLAlchemy
Instrumentor`` / ``HTTPXClientInstrumentor`` modify global module
state (patch the framework's classes), so a real activation in one
test would leak into all subsequent tests and break unrelated suites.

We therefore mock the three instrumentors and assert on the code path
behaviour: env-driven gating, idempotency, exporter selection.
"""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.sdk.trace.export import ConsoleSpanExporter

import app.core.tracing as tracing_module


@pytest.fixture(autouse=True)
def _isolate_tracing(monkeypatch):
    """Each test starts with a clean idempotency guard + mocked instrumentors."""
    monkeypatch.setattr(tracing_module, "_instrumented", False)
    # Mock the three instrumentors so we don't actually patch FastAPI /
    # SQLAlchemy / httpx globally during the test run.
    fake_fastapi = MagicMock()
    fake_sqla = MagicMock()
    fake_httpx = MagicMock()
    monkeypatch.setattr(tracing_module, "FastAPIInstrumentor", fake_fastapi)
    monkeypatch.setattr(tracing_module, "SQLAlchemyInstrumentor", fake_sqla)
    monkeypatch.setattr(tracing_module, "HTTPXClientInstrumentor", fake_httpx)
    yield {
        "fastapi": fake_fastapi,
        "sqlalchemy": fake_sqla,
        "httpx": fake_httpx,
    }


def test_setup_tracing_noop_when_disabled(monkeypatch, _isolate_tracing):
    """OTEL_ENABLED missing → setup is a no-op (does not crash)."""
    monkeypatch.delenv("OTEL_ENABLED", raising=False)
    app = FastAPI()
    tracing_module.setup_tracing(app)
    assert tracing_module._instrumented is False
    _isolate_tracing["fastapi"].instrument_app.assert_not_called()


def test_setup_tracing_noop_when_explicitly_false(monkeypatch, _isolate_tracing):
    monkeypatch.setenv("OTEL_ENABLED", "false")
    app = FastAPI()
    tracing_module.setup_tracing(app)
    assert tracing_module._instrumented is False
    _isolate_tracing["fastapi"].instrument_app.assert_not_called()


def test_setup_tracing_activates_and_calls_instrumentors(
    monkeypatch, _isolate_tracing
):
    """OTEL_ENABLED=true → instrumentors are called once."""
    monkeypatch.setenv("OTEL_ENABLED", "true")
    monkeypatch.setenv("OTEL_EXPORTER", "console")

    app = FastAPI()
    tracing_module.setup_tracing(app)
    assert tracing_module._instrumented is True
    _isolate_tracing["fastapi"].instrument_app.assert_called_once_with(app)
    _isolate_tracing["sqlalchemy"].return_value.instrument.assert_called_once()
    _isolate_tracing["httpx"].return_value.instrument.assert_called_once()


def test_setup_tracing_is_idempotent(monkeypatch, _isolate_tracing):
    """Second call is a no-op (guarded by _instrumented)."""
    monkeypatch.setenv("OTEL_ENABLED", "true")
    monkeypatch.setenv("OTEL_EXPORTER", "console")
    app = FastAPI()
    tracing_module.setup_tracing(app)
    tracing_module.setup_tracing(app)
    # Each instrumentor still called only once across both setup calls
    assert _isolate_tracing["fastapi"].instrument_app.call_count == 1


def test_exporter_selection_console_default(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER", raising=False)
    exporter = tracing_module._build_exporter()
    assert isinstance(exporter, ConsoleSpanExporter)


def test_exporter_selection_console_explicit(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER", "console")
    exporter = tracing_module._build_exporter()
    assert isinstance(exporter, ConsoleSpanExporter)


def test_exporter_selection_otlp(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER", "otlp")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    exporter = tracing_module._build_exporter()
    assert isinstance(exporter, OTLPSpanExporter)


def test_exporter_selection_otlp_uses_default_endpoint(monkeypatch):
    """OTLP exporter falls back to localhost:4318 when env var absent."""
    monkeypatch.setenv("OTEL_EXPORTER", "otlp")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    exporter = tracing_module._build_exporter()
    assert isinstance(exporter, OTLPSpanExporter)
