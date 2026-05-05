"""Sliding-window rate limit (algorithm + FastAPI dependency)."""

from collections.abc import Callable
from typing import Annotated

import fakeredis
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from redis import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedKey
from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.rate_limit import _check_and_record, enforce_rate_limit
from app.core.redis_client import get_redis
from app.services.jdr.db.models import ApiKey, ApiKeyStatus, Role


# ---- algorithm --------------------------------------------------------------


def test_check_and_record_allows_under_limit():
    redis_client = fakeredis.FakeStrictRedis()
    for _ in range(3):
        allowed, retry_after = _check_and_record(
            redis_client, "user-1", limit=5, window_seconds=60
        )
        assert allowed is True
        assert retry_after == 0


def test_check_and_record_blocks_at_limit():
    redis_client = fakeredis.FakeStrictRedis()
    for _ in range(5):
        allowed, _ = _check_and_record(
            redis_client, "user-1", limit=5, window_seconds=60
        )
        assert allowed is True

    allowed, retry_after = _check_and_record(
        redis_client, "user-1", limit=5, window_seconds=60
    )
    assert allowed is False
    assert retry_after >= 1


def test_check_and_record_isolates_buckets():
    redis_client = fakeredis.FakeStrictRedis()
    for _ in range(5):
        _check_and_record(redis_client, "user-1", limit=5, window_seconds=60)
    # Another user is unaffected by user-1 hitting the cap.
    allowed, _ = _check_and_record(
        redis_client, "user-2", limit=5, window_seconds=60
    )
    assert allowed is True


# ---- FastAPI dependency ----------------------------------------------------


@pytest_asyncio.fixture
async def seeded_gm_key(db_session: AsyncSession) -> str:
    """Insert one active GM key in the in-memory DB. Returns plaintext."""
    plain = "ratelimit-secret-key"
    hashed = PasswordHasher().hash(plain)
    db_session.add(
        ApiKey(
            name="ratelimit-test",
            hash=hashed,
            role=Role.GM,
            status=ApiKeyStatus.ACTIVE,
            pj_id=None,
        )
    )
    await db_session.commit()
    return plain


def _make_app(
    make_db_session_dep: Callable[..., object],
    redis_client: Redis,
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/protected")
    async def _protected(
        auth: Annotated[AuthenticatedKey, Depends(enforce_rate_limit)],
    ) -> dict[str, str]:
        return {"hello": auth.name}

    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = lambda: redis_client
    return app


async def test_rate_limit_allows_under_threshold(
    seeded_gm_key, make_db_session_dep, monkeypatch
):
    monkeypatch.setattr(
        "app.core.rate_limit.settings.RATE_LIMIT_PER_MINUTE", 3, raising=True
    )
    redis_client = fakeredis.FakeStrictRedis()
    app = _make_app(make_db_session_dep, redis_client)
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {seeded_gm_key}"}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(3):
            response = await client.get("/protected", headers=headers)
            assert response.status_code == 200


async def test_rate_limit_blocks_above_threshold(
    seeded_gm_key, make_db_session_dep, monkeypatch
):
    monkeypatch.setattr(
        "app.core.rate_limit.settings.RATE_LIMIT_PER_MINUTE", 2, raising=True
    )
    redis_client = fakeredis.FakeStrictRedis()
    app = _make_app(make_db_session_dep, redis_client)
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {seeded_gm_key}"}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/protected", headers=headers)
        await client.get("/protected", headers=headers)
        response = await client.get("/protected", headers=headers)

    assert response.status_code == 429
    assert response.headers["content-type"] == "application/problem+json"
    assert int(response.headers["retry-after"]) >= 1
    body = response.json()
    assert body["type"] == "https://kaeyris.local/errors/rate-limited"
    assert body["status"] == 429


async def test_rate_limit_blocks_unauthenticated_with_401_not_429(
    make_db_session_dep, monkeypatch
):
    monkeypatch.setattr(
        "app.core.rate_limit.settings.RATE_LIMIT_PER_MINUTE", 1, raising=True
    )
    redis_client = fakeredis.FakeStrictRedis()
    app = _make_app(make_db_session_dep, redis_client)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/protected")

    # The auth check runs before the rate limit, so an anonymous caller
    # gets a 401 — they don't even reach the rate limiter.
    assert response.status_code == 401
