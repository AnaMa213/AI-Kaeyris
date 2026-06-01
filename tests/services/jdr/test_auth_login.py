"""Web login contract tests for the JDR front."""

from collections.abc import Callable

import fakeredis
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.redis_client import get_redis
from app.services.jdr.auth_router import router as auth_router
from app.services.jdr.db.models import ApiKey, ApiKeyStatus, Role
from app.services.jdr.router import router as jdr_router


def _make_app(
    make_db_session_dep: Callable[..., object],
    redis_client: Redis,
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.include_router(jdr_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = lambda: redis_client
    return app


async def test_setup_create_user_login_and_cookie_authenticates_protected_route(
    make_db_session_dep,
):
    app = _make_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        setup = await client.post(
            "/services/jdr/auth/setup",
            json={"username": "admin", "password": "admin-password"},
        )
        assert setup.status_code == 201
        assert "httponly" in setup.headers["set-cookie"].lower()

        created = await client.post(
            "/services/jdr/users",
            json={
                "username": "alice",
                "profile": "gm",
                "password": "alice-password",
            },
        )
        assert created.status_code == 201
        assert "password_hash" not in created.text

        login = await client.post(
            "/services/jdr/auth/login",
            json={
                "username": "alice",
                "profile": "gm",
                "password": "alice-password",
            },
        )
        assert login.status_code == 200
        cookie = login.headers["set-cookie"]
        assert "session=" in cookie
        assert "httponly" in cookie.lower()
        assert "path=/" in cookie.lower()
        assert "samesite=lax" in cookie.lower()

        protected = await client.post(
            "/services/jdr/sessions",
            json={
                "title": "Session via cookie",
                "recorded_at": "2026-05-27T20:30:00+00:00",
            },
        )

    assert protected.status_code == 201


async def test_login_invalid_credentials_returns_exact_front_problem(
    make_db_session_dep,
):
    app = _make_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/services/jdr/auth/setup",
            json={"username": "admin", "password": "admin-password"},
        )
        response = await client.post(
            "/services/jdr/auth/login",
            json={"username": "admin", "profile": "gm", "password": "wrong"},
        )

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "about:blank",
        "title": "Invalid credentials",
        "status": 401,
    }


async def test_login_unsupported_profile_returns_exact_front_problem(
    make_db_session_dep,
):
    app = _make_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/services/jdr/auth/login",
            json={"username": "admin", "profile": "owner", "password": "anything"},
        )

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "about:blank",
        "title": "Forbidden",
        "status": 403,
    }


async def test_api_key_token_is_not_accepted_as_web_password(
    db_session: AsyncSession,
    make_db_session_dep,
):
    api_token = "legacy-api-token"
    db_session.add(
        ApiKey(
            name="legacy",
            hash=PasswordHasher().hash(api_token),
            role=Role.GM,
            status=ApiKeyStatus.ACTIVE,
        )
    )
    await db_session.commit()

    app = _make_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/services/jdr/auth/setup",
            json={"username": "admin", "password": "real-password"},
        )
        response = await client.post(
            "/services/jdr/auth/login",
            json={"username": "admin", "profile": "gm", "password": api_token},
        )

    assert response.status_code == 401


async def test_auth_request_bodies_remain_campaign_free(make_db_session_dep):
    app = _make_app(make_db_session_dep, fakeredis.FakeStrictRedis())

    schema = app.openapi()
    components = schema["components"]["schemas"]

    assert "campaign_id" not in components["SetupRequest"]["properties"]
    assert "campaign_id" not in components["LoginRequest"]["properties"]
    assert "campaign_id" not in components["UserCreate"]["properties"]
    assert "campaign_id" not in components["UserUpdate"]["properties"]
