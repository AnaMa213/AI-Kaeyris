"""Web-session validity and cookie-auth integration tests."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedKey, require_api_key, require_gm
from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.models import Profile, UserStatus
from app.core.users import (
    create_user,
    create_web_session,
    delete_user,
    revoke_web_session,
    validate_web_session,
)


def _make_cookie_app(make_db_session_dep: Callable[..., object]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/protected")
    async def protected(
        auth: Annotated[AuthenticatedKey, Depends(require_api_key)],
    ) -> dict[str, str]:
        return {"name": auth.name, "role": auth.role.value, "source": auth.source}

    @app.get("/gm-only", dependencies=[Depends(require_gm)])
    async def gm_only() -> dict[str, str]:
        return {"area": "gm"}

    app.dependency_overrides[get_db_session] = make_db_session_dep
    return app


async def test_validate_web_session_accepts_active_session(
    db_session: AsyncSession,
):
    user = await create_user(
        db_session,
        username="alice",
        profile=Profile.GM,
        password="secret",
    )
    token, _session = await create_web_session(
        db_session, user, ttl_seconds=3600
    )

    validated = await validate_web_session(db_session, token)

    assert validated is not None
    assert validated.user.username == "alice"


async def test_validate_web_session_rejects_expired_session(
    db_session: AsyncSession,
):
    user = await create_user(
        db_session,
        username="alice",
        profile=Profile.GM,
        password="secret",
    )
    token, web_session = await create_web_session(
        db_session, user, ttl_seconds=3600
    )
    web_session.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    assert await validate_web_session(db_session, token) is None


async def test_validate_web_session_rejects_revoked_session(
    db_session: AsyncSession,
):
    user = await create_user(
        db_session,
        username="alice",
        profile=Profile.GM,
        password="secret",
    )
    token, _session = await create_web_session(
        db_session, user, ttl_seconds=3600
    )
    await revoke_web_session(db_session, token)

    assert await validate_web_session(db_session, token) is None


async def test_validate_web_session_rejects_deleted_user(
    db_session: AsyncSession,
):
    user = await create_user(
        db_session,
        username="admin",
        profile=Profile.GM,
        password="secret",
    )
    other = await create_user(
        db_session,
        username="bob",
        profile=Profile.USER,
        password="secret",
    )
    token, _session = await create_web_session(
        db_session, other, ttl_seconds=3600
    )
    await delete_user(db_session, other.id)

    assert user.status == UserStatus.ACTIVE
    assert await validate_web_session(db_session, token) is None


async def test_cookie_auth_accepts_valid_web_session(
    db_session: AsyncSession,
    make_db_session_dep,
):
    user = await create_user(
        db_session,
        username="alice",
        profile=Profile.GM,
        password="secret",
    )
    token, _session = await create_web_session(
        db_session, user, ttl_seconds=3600
    )
    await db_session.commit()

    app = _make_cookie_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("session", token)
        response = await client.get("/protected")

    assert response.status_code == 200
    assert response.json() == {
        "name": "alice",
        "role": "gm",
        "source": "web_session",
    }
