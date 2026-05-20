"""Prometheus metrics for AI-Kaeyris (Jalon 6 — Observability §Phase 2).

Module-level metrics are registered into the default ``REGISTRY`` of
``prometheus_client``. Naming follows the Prometheus best practices
(https://prometheus.io/docs/practices/naming/):

- Prefix ``kaeyris_`` (the app namespace).
- ``_total`` suffix added automatically by ``prometheus_client.Counter``.
- Durations exposed as ``_seconds`` histograms — the unit is in the name,
  not a label.
- Labels chosen to keep cardinality bounded (no UUIDs, no raw URL paths
  — only route templates).

Set covered (intentionally minimal — YAGNI, ADR 0008):

1. **HTTP**: requests + duration (via middleware)
2. **LLM**: calls + tokens + duration (via app.adapters.llm)
3. **Transcription**: calls + duration (via app.adapters.transcription)
4. **RQ jobs**: invocations + duration (via app.jobs.jdr)

Anything more (Redis pool stats, DB pool stats, GC stats…) is left to
``prometheus_client`` defaults or future iteration.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

# ---------------------------------------------------------------------------
# HTTP — instrumented by app.core.metrics_middleware.MetricsMiddleware
# ---------------------------------------------------------------------------

HTTP_REQUESTS_TOTAL = Counter(
    "kaeyris_http_requests_total",
    "Number of HTTP requests handled, by method, route template and status.",
    labelnames=("method", "route", "status"),
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "kaeyris_http_request_duration_seconds",
    "HTTP request latency from receive to response, in seconds.",
    labelnames=("method", "route"),
    # Buckets tuned for a local API: most calls are < 100ms; transcription
    # & LLM endpoints can spike to several seconds (jobs return 202 fast,
    # but synchronous artefact GETs may include LLM-influenced state checks).
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# ---------------------------------------------------------------------------
# LLM — instrumented in app.adapters.llm.OpenAICompatibleLLMAdapter.complete
# ---------------------------------------------------------------------------

LLM_CALLS_TOTAL = Counter(
    "kaeyris_llm_calls_total",
    "Number of LLM `complete` calls, by provider / model / outcome.",
    labelnames=("provider", "model", "outcome"),  # outcome: success | transient | permanent
)

LLM_TOKENS_TOTAL = Counter(
    "kaeyris_llm_tokens_total",
    "Total LLM tokens billed, by provider / model / direction (prompt | completion).",
    labelnames=("provider", "model", "direction"),
)

LLM_CALL_DURATION_SECONDS = Histogram(
    "kaeyris_llm_call_duration_seconds",
    "Duration of LLM `complete` calls in seconds.",
    labelnames=("provider", "model"),
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)

# ---------------------------------------------------------------------------
# Transcription — instrumented in app.adapters.transcription.OpenAICompatible
# ---------------------------------------------------------------------------

TRANSCRIPTION_CALLS_TOTAL = Counter(
    "kaeyris_transcription_calls_total",
    "Number of transcription adapter calls, by provider / outcome.",
    labelnames=("provider", "outcome"),  # outcome: success | transient | permanent
)

TRANSCRIPTION_DURATION_SECONDS = Histogram(
    "kaeyris_transcription_duration_seconds",
    "Duration of transcription adapter calls in seconds.",
    labelnames=("provider",),
    buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0),
)

# ---------------------------------------------------------------------------
# RQ jobs — instrumented in app.jobs.jdr (sync wrappers)
# ---------------------------------------------------------------------------

JOBS_TOTAL = Counter(
    "kaeyris_jobs_total",
    "Number of RQ jobs executed, by kind / outcome.",
    labelnames=("kind", "outcome"),  # kind: transcription | narrative | elements | povs | summary
)

JOB_DURATION_SECONDS = Histogram(
    "kaeyris_job_duration_seconds",
    "End-to-end duration of an RQ job in seconds (wrapper to commit).",
    labelnames=("kind",),
    buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0),
)
