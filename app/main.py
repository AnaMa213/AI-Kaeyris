"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.auth import bootstrap_api_keys_from_env
from app.core.config import settings
from app.core.db import get_sessionmaker
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.request_context import RequestContextMiddleware
from app.core.security_headers import SecurityHeadersMiddleware
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
# RequestContextMiddleware is added AFTER SecurityHeadersMiddleware so it
# runs FIRST in the request flow (Starlette stacks middlewares in reverse).
# The request_id contextvar must be bound before any application code runs.
app.add_middleware(RequestContextMiddleware)
register_exception_handlers(app)
app.include_router(jdr_router)


@app.get("/health", tags=["health"], summary="Vérifie que l'API est en vie.")
def health() -> dict[str, str]:
    return {"status": "ok", "version": settings.APP_VERSION}
