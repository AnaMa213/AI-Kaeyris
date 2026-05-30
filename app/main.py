"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, status
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import bootstrap_api_keys_from_env
from app.core.config import settings
from app.core.db import get_db_session, get_sessionmaker
from app.core.errors import register_exception_handlers
from app.core.health import check_database, check_redis
from app.core.logging import configure_logging, get_logger
from app.core.metrics_middleware import MetricsMiddleware
from app.core.redis_client import get_redis
from app.core.request_context import RequestContextMiddleware
from app.core.security_headers import SecurityHeadersMiddleware
from app.core.tracing import setup_tracing
from app.services.jdr.auth_router import router as jdr_auth_router
from app.services.jdr.router import router as jdr_router

# Configure structured logging as early as possible — before any logger is
# actually used. Reads LOG_FORMAT / LOG_LEVEL env vars (cf. core/logging.py).
configure_logging()

logger = get_logger(__name__)


async def _run_startup_tasks() -> None:
    """One-shot tasks executed at application startup.

    Importing the legacy ``API_KEYS`` env var into ``jdr_api_keys`` if the
    table is empty (ADR 0006 §3 — env var becomes a bootstrap-only path
    after jalon 5). Failure here is logged but never crashes the app:
    a misconfigured env var should not prevent the API from starting.
    """
    try:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            inserted = await bootstrap_api_keys_from_env(session)
            if inserted:
                logger.info("startup.api_keys_bootstrapped", inserted=inserted)
    except Exception as exc:  # noqa: BLE001 — log and continue
        logger.error(
            "startup.api_keys_bootstrap_failed",
            error=str(exc),
            exc_info=exc,
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Application lifespan — runs once on startup, once on shutdown."""
    await _run_startup_tasks()
    yield
    # Nothing to dispose on shutdown yet (engines are cached at module level).


app = FastAPI(
    title="AI-Kaeyris",
    version=settings.APP_VERSION,
    description="Plateforme AI personnelle — monolithe modulaire FastAPI.",
    lifespan=lifespan,
)
app.add_middleware(SecurityHeadersMiddleware)
# Middleware stacking note: Starlette executes middlewares in REVERSE order
# of registration, so the last added is the first to run. We want
# RequestContextMiddleware to run first (so request_id is bound for every
# downstream log), followed by MetricsMiddleware (to measure the full
# pipeline including auth/rate-limit etc.).
app.add_middleware(MetricsMiddleware)
app.add_middleware(RequestContextMiddleware)
if settings.cors_allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
register_exception_handlers(app)
app.include_router(jdr_auth_router)
app.include_router(jdr_router)

# OpenTelemetry tracing setup — no-op unless OTEL_ENABLED=true.
# Instruments FastAPI / SQLAlchemy / httpx automatically when active.
# Must be called AFTER the router is mounted so FastAPIInstrumentor
# sees every route.
setup_tracing(app)


@app.get("/health", tags=["health"], summary="Liveness alias (legacy Jalon 0).")
def health() -> dict[str, str]:
    """Legacy liveness endpoint kept for backward compatibility.

    Equivalent to ``/healthz``. New monitoring setups should target
    ``/healthz`` (liveness) and ``/readyz`` (readiness) instead.
    """
    return {"status": "ok", "version": settings.APP_VERSION}


@app.get("/healthz", tags=["health"], summary="Liveness probe (Jalon 6).")
def healthz() -> dict[str, str]:
    """Always 200 as long as the Python process is alive.

    Does NOT check external dependencies. Use ``/readyz`` to decide
    whether to send traffic. Intent: orchestrator should restart the
    process iff this fails.
    """
    return {"status": "ok", "version": settings.APP_VERSION}


@app.get(
    "/readyz",
    tags=["health"],
    summary="Readiness probe — DB + Redis (Jalon 6).",
)
async def readyz(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    redis_client: Annotated[Redis, Depends(get_redis)],
) -> Response:
    """200 if every backing dependency is reachable, 503 otherwise.

    Per-check status surfaced in the body so an operator can see
    which dependency is down without parsing logs.
    """
    db_ok, db_err = await check_database(db)
    redis_ok, redis_err = await check_redis(redis_client)

    checks = {
        "database": "ok" if db_ok else f"fail: {db_err}",
        "redis": "ok" if redis_ok else f"fail: {redis_err}",
    }
    overall_ok = db_ok and redis_ok
    payload = {
        "status": "ok" if overall_ok else "fail",
        "checks": checks,
    }
    http_status = (
        status.HTTP_200_OK if overall_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return JSONResponse(content=payload, status_code=http_status)


@app.get(
    "/metrics",
    tags=["observability"],
    summary="Prometheus text exposition (Jalon 6).",
    include_in_schema=False,
)
def metrics() -> Response:
    """Expose Prometheus metrics in the default text-based format.

    Excluded from the OpenAPI schema (``include_in_schema=False``)
    because clients don't consume it — it's scraped by Prometheus /
    a sidecar.
    """
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
