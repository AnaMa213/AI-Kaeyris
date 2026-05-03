"""Redis client factory.

ADR 0004. The same client is shared by FastAPI dependencies (rate limit)
and the job machinery (queue, results). Tests override `get_redis` via
``app.dependency_overrides`` to inject ``fakeredis``.
"""

from functools import lru_cache

from redis import Redis

from app.core.config import settings


@lru_cache(maxsize=1)
def _build_client() -> Redis:
    return Redis.from_url(settings.REDIS_URL, decode_responses=False)


def get_redis() -> Redis:
    """FastAPI dependency: returns a process-wide Redis client."""
    return _build_client()
