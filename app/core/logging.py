"""Structured logging setup (Jalon 6 — Observability §Phase 1).

Bridges stdlib ``logging`` to ``structlog`` so every log call across the
codebase ends up as a single line of JSON in production (cohérent
12-Factor §XI) and as a human-readable line in development.

Conventions adopted:

- **Event name** = first positional arg, snake_case, dotted namespace
  (``startup.api_keys_bootstrapped``, ``job.transcribe.start``).
- **Context** passed as kwargs (``session_id=...``, ``job_id=...``,
  ``duration_ms=...``). Never f-strings in event names.
- ``contextvars`` populated by the request middleware (request_id) and
  bound manually in jobs (session_id, job_id). Auto-propagated to every
  log emitted while the contextvar is alive.

Output mode:
- ``LOG_FORMAT=json`` (default in prod / Docker) → one-line JSON
- ``LOG_FORMAT=console`` (default in dev / local venv) → coloured human-
  readable lines

See: https://www.structlog.org/en/stable/standard-library.html
"""

from __future__ import annotations

import logging
import os
import sys

import structlog


def configure_logging(*, json_mode: bool | None = None, level: int | None = None) -> None:
    """Configure stdlib + structlog. Idempotent — safe to call twice.

    Args:
        json_mode: ``True`` for one-line JSON, ``False`` for human-readable
            console. ``None`` reads ``LOG_FORMAT`` env var (default
            ``"console"`` if absent, switch to ``"json"`` in prod).
        level: log level (default ``logging.INFO`` or ``LOG_LEVEL`` env).
    """
    if json_mode is None:
        json_mode = os.environ.get("LOG_FORMAT", "console").lower() == "json"
    if level is None:
        level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_name, logging.INFO)

    # 1) Stdlib root logger: write to stderr with a minimal formatter
    #    (structlog handles the rendering itself).
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=level,
        force=True,
    )

    # Reduce verbosity of noisy libs unless explicitly raised
    for noisy in ("httpx", "httpcore", "openai._base_client", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # 2) Structlog processor chain (shared by stdlib + structlog loggers)
    shared_processors: list[structlog.types.Processor] = [
        # Auto-merge anything bound via structlog.contextvars.bind_contextvars(...)
        # (request_id, session_id, job_id...).
        structlog.contextvars.merge_contextvars,
        # Add a per-event timestamp (ISO 8601, UTC).
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        # Surface the log level + logger name in every record.
        structlog.processors.add_log_level,
        structlog.stdlib.add_logger_name,
        # Render the exception traceback as a "exception" key when relevant.
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
    ]

    if json_mode:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Drop-in replacement for ``logging.getLogger(__name__)``.

    Returns a structlog bound logger that surfaces the module name in
    every record and merges any contextvars already bound.
    """
    return structlog.get_logger(name)  # type: ignore[return-value]
