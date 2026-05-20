"""Health & readiness checks (Jalon 6 — Observability §Phase 3).

Two endpoints, Kubernetes-style convention:

- ``/healthz`` — **liveness** probe. Returns 200 as long as the Python
  process is alive and Starlette can route. Never checks external
  dependencies. Intent: "should I restart the process?".

- ``/readyz`` — **readiness** probe. Pings DB + Redis. Returns 200 only
  if every dependency is reachable; otherwise 503 with the per-check
  status detail in the body. Intent: "should I send traffic here?".

The legacy ``/health`` endpoint (Jalon 0) is kept as an alias of
``/healthz`` for backward compatibility — no automated client should
need to switch, but new monitoring setups should target ``/healthz``
and ``/readyz``.

References:
- Kubernetes probe conventions: https://kubernetes.io/docs/concepts/configuration/liveness-readiness-startup-probes/
- "Healthchecks vs readiness" — Honeycomb blog: https://www.honeycomb.io/blog/observability-101-health-checks
"""

from __future__ import annotations

import asyncio

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession


async def check_database(session: AsyncSession) -> tuple[bool, str | None]:
    """Ping the DB with ``SELECT 1``.

    Returns ``(True, None)`` on success, ``(False, "<error message>")``
    on any SQLAlchemy or driver-level failure. Never raises — the
    error is captured in the second element of the tuple.
    """
    try:
        result = await session.execute(text("SELECT 1"))
        # Force materialisation of the result so we actually round-trip.
        _ = result.scalar()
        return True, None
    except SQLAlchemyError as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _ping_redis_sync(client: Redis) -> None:
    """Synchronous PING (redis-py is sync). Raises RedisError on failure."""
    client.ping()


async def check_redis(client: Redis) -> tuple[bool, str | None]:
    """Ping Redis from the async loop without blocking it.

    ``redis-py`` is a sync library; we hop to a thread for the PING so
    we don't block the event loop even if Redis is slow/unreachable.
    """
    try:
        await asyncio.to_thread(_ping_redis_sync, client)
        return True, None
    except RedisError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    except OSError as exc:
        # Connection refused / DNS failure surface as OSError before
        # redis-py wraps them.
        return False, f"{type(exc).__name__}: {exc}"
