"""Tests for /healthz and /readyz endpoints (Jalon 6 — Phase 3)."""

from collections.abc import Callable
from typing import Any

import fakeredis
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.db import get_db_session
from app.core.redis_client import get_redis
from app.main import app as production_app


# ---------------------------------------------------------------------------
# /healthz — liveness (never depends on externals)
# ---------------------------------------------------------------------------


async def test_healthz_always_returns_200():
    """No deps, no DB, no Redis — just a smoke test that the process responds."""
    transport = ASGITransport(app=production_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body


async def test_legacy_health_alias_still_works():
    """The Jalon 0 /health endpoint must keep working (compat)."""
    transport = ASGITransport(app=production_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# /readyz — depends on DB + Redis
# ---------------------------------------------------------------------------


def _make_readyz_app(make_db_session_dep: Callable[..., Any]) -> FastAPI:
    """Use the production app but override DB + Redis dependencies for tests."""
    production_app.dependency_overrides[get_db_session] = make_db_session_dep
    production_app.dependency_overrides[get_redis] = lambda: fakeredis.FakeStrictRedis()
    return production_app


@pytest.fixture(autouse=True)
def _clear_overrides():
    """Make sure dependency_overrides don't leak between tests."""
    yield
    production_app.dependency_overrides.clear()


async def test_readyz_returns_200_when_db_and_redis_ok(
    db_session, make_db_session_dep
):
    app = _make_readyz_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"] == "ok"


async def test_readyz_returns_503_when_redis_unreachable(
    db_session, make_db_session_dep
):
    """A Redis client pointing nowhere should make /readyz fail with 503."""

    class _FailingRedis:
        def ping(self):
            raise ConnectionError("redis is down")

    production_app.dependency_overrides[get_db_session] = make_db_session_dep
    production_app.dependency_overrides[get_redis] = lambda: _FailingRedis()

    transport = ASGITransport(app=production_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "fail"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"].startswith("fail:")
    assert "redis is down" in body["checks"]["redis"]
