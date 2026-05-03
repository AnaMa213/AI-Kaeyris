"""Sliding-window rate limiting backed by a Redis sorted set.

ADR 0004. One bucket per authenticated API key name. Run AFTER
``require_api_key`` so the bucket is the validated identity, never a
client-controlled value.
"""

import secrets
import time
from typing import Annotated

from fastapi import Depends, status
from redis import Redis

from app.core.auth import AuthenticatedKey, require_api_key
from app.core.config import settings
from app.core.errors import AppError
from app.core.redis_client import get_redis

_KEY_PREFIX = "ratelimit:"


class RateLimitedError(AppError):
    """Raised when a client exceeds its allowed call rate."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    error_type = "rate-limited"
    title = "Too Many Requests"


def _check_and_record(
    redis_client: Redis,
    bucket: str,
    *,
    limit: int,
    window_seconds: int,
) -> tuple[bool, int]:
    """Return ``(allowed, retry_after_seconds)``.

    ``retry_after_seconds`` is meaningful only when ``allowed`` is False.
    The race window between the count and the insert is microscopic at our
    scale; if it ever matters we'll move to a Lua script.
    """
    now = time.time()
    cutoff = now - window_seconds
    key = f"{_KEY_PREFIX}{bucket}"

    pipe = redis_client.pipeline()
    pipe.zremrangebyscore(key, 0, cutoff)
    pipe.zcard(key)
    _, count = pipe.execute()

    if count >= limit:
        oldest = redis_client.zrange(key, 0, 0, withscores=True)
        if oldest:
            _, oldest_score = oldest[0]
            retry_after = max(1, int(oldest_score + window_seconds - now) + 1)
        else:
            retry_after = window_seconds
        return False, retry_after

    member = f"{now}:{secrets.token_hex(8)}"
    pipe = redis_client.pipeline()
    pipe.zadd(key, {member: now})
    pipe.expire(key, window_seconds)
    pipe.execute()
    return True, 0


def enforce_rate_limit(
    auth: Annotated[AuthenticatedKey, Depends(require_api_key)],
    redis_client: Annotated[Redis, Depends(get_redis)],
) -> AuthenticatedKey:
    """FastAPI dependency: rate-limit by authenticated API key name.

    Usage::

        app.include_router(
            svc.router,
            dependencies=[Depends(enforce_rate_limit)],
        )

    ``enforce_rate_limit`` itself depends on ``require_api_key`` so the
    auth check runs first; FastAPI's per-request dependency cache means
    that adding both in ``dependencies=`` is also safe (auth runs once).
    """
    allowed, retry_after = _check_and_record(
        redis_client,
        auth.name,
        limit=settings.RATE_LIMIT_PER_MINUTE,
        window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
    )
    if not allowed:
        raise RateLimitedError(
            detail=f"Rate limit exceeded. Retry after {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )
    return auth
