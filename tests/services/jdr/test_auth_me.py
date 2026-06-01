"""Current authenticated web context endpoint tests."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.models import Profile, UserStatus
from app.core.users import create_web_session
from app.services.jdr.auth_router import router as auth_router
from app.services.jdr.db.models import CampaignRole
from tests.services.jdr.campaign_fixtures import (
    make_campaign,
    make_membership,
    make_pj,
    make_user,
    make_web_session,
)
from tests.services.jdr.test_datetime_serialization import (
    assert_datetime_fields_have_explicit_timezone,
)


def _make_app(make_db_session_dep: Callable[..., object]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    return app


async def test_auth_me_returns_gm_identity_and_active_campaign(
    db_session,
    make_db_session_dep,
):
    gm = await make_user(db_session, username="admin", profile=Profile.GM)
    campaign = await make_campaign(db_session, owner=gm)
    await make_membership(db_session, user=gm, campaign=campaign, role=CampaignRole.GM)
    token = await make_web_session(db_session, user=gm)
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("session", token)
        response = await client.get("/services/jdr/auth/me")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert_datetime_fields_have_explicit_timezone(response.json())
    assert response.json() == {
        "user": {"id": str(gm.id), "username": "admin"},
        "active_campaign": {
            "id": str(campaign.id),
            "name": campaign.name,
            "role": "gm",
            "character_id": None,
        },
    }


async def test_auth_me_returns_player_membership_with_character(
    db_session,
    make_db_session_dep,
):
    gm = await make_user(db_session, username="admin", profile=Profile.GM)
    player = await make_user(db_session, username="alice", profile=Profile.USER)
    campaign = await make_campaign(db_session, owner=gm)
    pj = await make_pj(db_session, owner=gm, campaign=campaign)
    await make_membership(
        db_session,
        user=player,
        campaign=campaign,
        role=CampaignRole.PLAYER,
        character=pj,
    )
    token = await make_web_session(db_session, user=player)
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("session", token)
        response = await client.get("/services/jdr/auth/me")

    assert response.status_code == 200
    assert response.json()["active_campaign"] == {
        "id": str(campaign.id),
        "name": campaign.name,
        "role": "player",
        "character_id": str(pj.id),
    }


async def test_auth_me_allows_active_user_without_membership(
    db_session,
    make_db_session_dep,
):
    user = await make_user(db_session, username="orphan", profile=Profile.USER)
    token = await make_web_session(db_session, user=user)
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("session", token)
        response = await client.get("/services/jdr/auth/me")

    assert response.status_code == 200
    assert response.json() == {
        "user": {"id": str(user.id), "username": "orphan"},
        "active_campaign": None,
    }


async def test_auth_me_rejects_missing_cookie(make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/services/jdr/auth/me")

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing or malformed credentials."


async def test_auth_me_rejects_expired_revoked_and_deleted_sessions(
    db_session,
    make_db_session_dep,
):
    user = await make_user(db_session, username="alice", profile=Profile.USER)
    expired_token, expired = await create_web_session(
        db_session,
        user,
        ttl_seconds=3600,
    )
    expired.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    revoked_token, revoked = await create_web_session(
        db_session,
        user,
        ttl_seconds=3600,
    )
    revoked.revoked_at = datetime.now(UTC)
    deleted_token, _deleted_session = await create_web_session(
        db_session,
        user,
        ttl_seconds=3600,
    )
    user.status = UserStatus.DELETED
    await db_session.commit()

    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for token in (expired_token, revoked_token, deleted_token):
            client.cookies.set("session", token)
            response = await client.get("/services/jdr/auth/me")
            assert response.status_code == 401


async def test_auth_me_does_not_expose_secrets(db_session, make_db_session_dep):
    user = await make_user(db_session, username="alice", profile=Profile.USER)
    token = await make_web_session(db_session, user=user)
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("session", token)
        response = await client.get("/services/jdr/auth/me")

    assert response.status_code == 200
    assert "password_hash" not in response.text
    assert "token_hash" not in response.text
    assert token not in response.text


async def test_auth_me_is_present_in_openapi(make_db_session_dep):
    app = _make_app(make_db_session_dep)

    schema = app.openapi()

    assert "/services/jdr/auth/me" in schema["paths"]
    assert "get" in schema["paths"]["/services/jdr/auth/me"]
    assert "/services/jdr/auth/setup" in schema["paths"]
    assert "/services/jdr/auth/login" in schema["paths"]
    assert "/services/jdr/auth/logout" in schema["paths"]
    assert "/services/jdr/users" in schema["paths"]
