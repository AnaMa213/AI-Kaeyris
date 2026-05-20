"""OpenTelemetry tracing setup (Jalon 6 — Observability §Phase 4).

Minimal, opt-in tracer provider configuration. Wires auto-instrumentation
for FastAPI / SQLAlchemy / httpx without adding any manual spans at this
stage — manual spans on the LLM map/reduce pipeline are deferred to
Jalon 8 (deployment), where a real collector (Jaeger / Tempo / OTLP
collector) will be brought up alongside the API.

Activation:

- ``OTEL_ENABLED=false`` (default) — no-op. The OpenTelemetry SDK does
  not even create a real tracer provider; the global tracer remains the
  no-op default that prometheus_client / FastAPI handle gracefully.
- ``OTEL_ENABLED=true`` — sets up a real tracer provider with the
  configured exporter and applies the three instrumentations once.

Exporter selection (only when enabled):

- ``OTEL_EXPORTER=console`` (default) — spans written to stdout via the
  built-in :class:`ConsoleSpanExporter`. Useful for local debugging
  without bringing up a collector.
- ``OTEL_EXPORTER=otlp`` — OTLP/HTTP exporter to the URL declared in
  ``OTEL_EXPORTER_OTLP_ENDPOINT`` (default ``http://localhost:4318``).
  This is the path for production: a Docker Compose sidecar collector
  (Jaeger all-in-one, Tempo, OTEL Collector) consumes the spans.

Service identity:

- ``OTEL_SERVICE_NAME`` (default ``ai-kaeyris``) — name surfaced in the
  ``service.name`` resource attribute. Standard convention so the
  spans land in the right trace view.

References:
- OpenTelemetry Python: https://opentelemetry.io/docs/languages/python/
- FastAPI instrumentation: https://opentelemetry.io/docs/zero-code/python/
- Semantic conventions service.*: https://opentelemetry.io/docs/specs/semconv/resource/
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SpanExporter,
)

from app.core.db import get_engine

_instrumented = False  # idempotency guard


def setup_tracing(app: FastAPI) -> None:
    """Configure OpenTelemetry tracing if ``OTEL_ENABLED=true``.

    No-op when disabled (default). Idempotent — safe to call multiple
    times across tests + main. Instrumentations are applied at most
    once globally; subsequent calls are silently skipped.
    """
    global _instrumented

    if os.environ.get("OTEL_ENABLED", "false").lower() != "true":
        return
    if _instrumented:
        return

    service_name = os.environ.get("OTEL_SERVICE_NAME", "ai-kaeyris")
    resource = Resource.create({SERVICE_NAME: service_name})

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(_build_exporter()))
    trace.set_tracer_provider(provider)

    # Auto-instrumentation. Each instrumentor is idempotent under its own
    # guard but our `_instrumented` flag avoids any double-call risk.
    FastAPIInstrumentor.instrument_app(app)
    SQLAlchemyInstrumentor().instrument(engine=get_engine().sync_engine)
    HTTPXClientInstrumentor().instrument()

    _instrumented = True


def _build_exporter() -> SpanExporter:
    """Pick the exporter according to ``OTEL_EXPORTER`` env var."""
    exporter_kind = os.environ.get("OTEL_EXPORTER", "console").lower()
    if exporter_kind == "otlp":
        endpoint = os.environ.get(
            "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318"
        )
        # Tracesexport path is /v1/traces by spec — the HTTP exporter
        # appends it automatically when given a base endpoint.
        return OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces")
    return ConsoleSpanExporter()
