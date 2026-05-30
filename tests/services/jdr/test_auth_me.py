"""GET /services/jdr/auth/me contract tests."""

from collections.abc import Callable

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.models import Profile
from app.core.users import create_web_session
from app.services.jdr.auth_router import router as auth_router
from app.services.jdr.db.models import CampaignRole
from tests.services.jdr.campaign_factories import (
    make_campaign,
    make_membership,
    make_user,
)


def _make_app(make_db_session_dep: Callable[..., object]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    return app


async def _client_for_user(db_session, app, user):
    token, _ = await create_web_session(
        db_session,
        user,
        ttl_seconds=3600,
    )
    await db_session.commit()
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    client.cookies.set(settings.SESSION_COOKIE_NAME, token)
    return client


async def test_auth_me_returns_mj_campaign_context(db_session, make_db_session_dep):
    gm = await make_user(db_session, username="gm", profile=Profile.GM)
    campaign = await make_campaign(db_session, owner=gm)
    await make_membership(
        db_session,
        user=gm,
        campaign=campaign,
        role=CampaignRole.MJ,
    )
    app = _make_app(make_db_session_dep)

    async with await _client_for_user(db_session, app, gm) as client:
        response = await client.get("/services/jdr/auth/me")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "user": {"id": str(gm.id), "username": "gm"},
        "active_campaign": {
            "id": str(campaign.id),
            "name": campaign.name,
            "role": "mj",
            "character_id": None,
        },
    }


async def test_auth_me_returns_player_campaign_context(db_session, make_db_session_dep):
    gm = await make_user(db_session, username="gm", profile=Profile.GM)
    player = await make_user(db_session, username="player")
    campaign = await make_campaign(db_session, owner=gm)
    await make_membership(
        db_session,
        user=player,
        campaign=campaign,
        role=CampaignRole.PLAYER,
    )
    app = _make_app(make_db_session_dep)

    async with await _client_for_user(db_session, app, player) as client:
        response = await client.get("/services/jdr/auth/me")

    assert response.status_code == 200
    assert response.json()["active_campaign"]["role"] == "player"


async def test_auth_me_returns_null_campaign_for_user_without_membership(
    db_session,
    make_db_session_dep,
):
    user = await make_user(db_session, username="lonely")
    app = _make_app(make_db_session_dep)

    async with await _client_for_user(db_session, app, user) as client:
        response = await client.get("/services/jdr/auth/me")

    assert response.status_code == 200
    assert response.json()["active_campaign"] is None


async def test_auth_me_requires_web_session(make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/services/jdr/auth/me")

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
