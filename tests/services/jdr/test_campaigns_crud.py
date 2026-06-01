"""Campaign CRUD endpoint tests for BD-6."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import fakeredis
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis import Redis
from sqlalchemy import select

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.models import Profile
from app.core.redis_client import get_redis
from app.services.jdr.auth_router import router as auth_router
from app.services.jdr.db.models import Campaign, CampaignMember, CampaignRole, Session
from app.services.jdr.router import router as jdr_router
from tests.services.jdr.campaign_fixtures import (
    make_campaign,
    make_membership,
    make_session,
    make_user,
    make_web_session,
)
from tests.services.jdr.test_datetime_serialization import (
    assert_datetime_fields_have_explicit_timezone,
)


def _make_app(
    make_db_session_dep: Callable[..., object],
    redis_client: Redis | None = None,
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.include_router(jdr_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = lambda: redis_client or fakeredis.FakeStrictRedis()
    return app


async def _client_for_user(
    db_session,
    make_db_session_dep,
    *,
    username: str = "gm",
    profile: Profile = Profile.GM,
):
    user = await make_user(db_session, username=username, profile=profile)
    token = await make_web_session(db_session, user=user)
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    client.cookies.set("session", token)
    return user, client


async def test_list_campaigns_returns_memberships_with_aggregates(
    db_session,
    make_db_session_dep,
):
    gm, client = await _client_for_user(db_session, make_db_session_dep)
    campaign_a = await make_campaign(
        db_session,
        owner=gm,
        name="A",
        description="Visible",
    )
    campaign_b = await make_campaign(db_session, owner=gm, name="B")
    foreign_owner = await make_user(db_session, username="other", profile=Profile.GM)
    await make_campaign(db_session, owner=foreign_owner, name="Foreign")
    await make_membership(db_session, user=gm, campaign=campaign_a, role=CampaignRole.GM)
    await make_membership(
        db_session,
        user=gm,
        campaign=campaign_b,
        role=CampaignRole.PLAYER,
    )
    latest = datetime(2026, 5, 29, 18, 30, tzinfo=UTC)
    await make_session(db_session, owner=gm, campaign=campaign_a, recorded_at=latest)
    await db_session.commit()

    async with client:
        response = await client.get("/services/jdr/campaigns")

    assert response.status_code == 200
    assert_datetime_fields_have_explicit_timezone(response.json())
    assert response.json()["total"] == 2
    assert response.json()["size"] == 50
    items = response.json()["items"]
    assert [item["name"] for item in items] == ["A", "B"]
    assert items[0]["description"] == "Visible"
    assert items[0]["role"] == "gm"
    assert items[0]["session_count"] == 1
    assert items[0]["last_session_at"].endswith(("Z", "+00:00"))
    assert items[1]["role"] == "player"
    assert items[1]["session_count"] == 0
    assert items[1]["last_session_at"] is None


async def test_get_campaign_requires_membership(db_session, make_db_session_dep):
    gm, client = await _client_for_user(db_session, make_db_session_dep)
    campaign = await make_campaign(db_session, owner=gm, name="Visible")
    foreign = await make_campaign(db_session, owner=gm, name="Hidden")
    await make_membership(db_session, user=gm, campaign=campaign)
    await db_session.commit()

    async with client:
        ok = await client.get(f"/services/jdr/campaigns/{campaign.id}")
        forbidden = await client.get(f"/services/jdr/campaigns/{foreign.id}")
        missing = await client.get(f"/services/jdr/campaigns/{uuid4()}")

    assert ok.status_code == 200
    assert ok.json()["name"] == "Visible"
    assert forbidden.status_code == 403
    assert missing.status_code == 404


async def test_create_campaign_creates_gm_membership(db_session, make_db_session_dep):
    gm, client = await _client_for_user(db_session, make_db_session_dep)

    async with client:
        created = await client.post(
            "/services/jdr/campaigns",
            json={"name": " Les Royaumes Brises ", "description": "V1"},
        )
        duplicate = await client.post(
            "/services/jdr/campaigns",
            json={"name": "les royaumes brises"},
        )
        invalid = await client.post("/services/jdr/campaigns", json={"name": "   "})

    assert created.status_code == 201
    assert_datetime_fields_have_explicit_timezone(created.json())
    body = created.json()
    assert body["name"] == "Les Royaumes Brises"
    assert body["description"] == "V1"
    assert body["role"] == "gm"
    assert body["session_count"] == 0
    membership = await db_session.get(
        CampaignMember,
        {"user_id": gm.id, "campaign_id": UUID(body["id"])},
    )
    assert membership is not None
    assert membership.role == CampaignRole.GM
    assert duplicate.status_code == 409
    assert invalid.status_code == 422


async def test_patch_campaign_requires_gm_membership(db_session, make_db_session_dep):
    gm, client = await _client_for_user(db_session, make_db_session_dep)
    player = await make_user(db_session, username="player", profile=Profile.USER)
    player_token = await make_web_session(db_session, user=player)
    campaign = await make_campaign(db_session, owner=gm, name="A")
    duplicate = await make_campaign(db_session, owner=gm, name="B")
    await make_membership(db_session, user=gm, campaign=campaign, role=CampaignRole.GM)
    await make_membership(db_session, user=gm, campaign=duplicate, role=CampaignRole.GM)
    await make_membership(
        db_session,
        user=player,
        campaign=campaign,
        role=CampaignRole.PLAYER,
    )
    await db_session.commit()

    player_app = _make_app(make_db_session_dep)
    player_client = AsyncClient(
        transport=ASGITransport(app=player_app),
        base_url="http://test",
    )
    player_client.cookies.set("session", player_token)

    async with client, player_client:
        updated = await client.patch(
            f"/services/jdr/campaigns/{campaign.id}",
            json={"name": "A2", "description": None},
        )
        conflict = await client.patch(
            f"/services/jdr/campaigns/{campaign.id}",
            json={"name": " b "},
        )
        forbidden = await player_client.patch(
            f"/services/jdr/campaigns/{campaign.id}",
            json={"description": "Nope"},
        )

    assert updated.status_code == 200
    assert updated.json()["name"] == "A2"
    assert updated.json()["description"] is None
    assert conflict.status_code == 409
    assert forbidden.status_code == 403


async def test_delete_campaign_refuses_campaign_with_sessions(
    db_session,
    make_db_session_dep,
):
    gm, client = await _client_for_user(db_session, make_db_session_dep)
    empty = await make_campaign(db_session, owner=gm, name="Empty")
    used = await make_campaign(db_session, owner=gm, name="Used")
    await make_membership(db_session, user=gm, campaign=empty)
    await make_membership(db_session, user=gm, campaign=used)
    await make_session(db_session, owner=gm, campaign=used)
    empty_id = empty.id
    used_id = used.id
    await db_session.commit()

    async with client:
        deleted = await client.delete(f"/services/jdr/campaigns/{empty_id}")
        conflict = await client.delete(f"/services/jdr/campaigns/{used_id}")

    assert deleted.status_code == 204
    db_session.expire_all()
    assert await db_session.get(Campaign, empty_id) is None
    assert conflict.status_code == 409
    assert await db_session.get(Campaign, used_id) is not None
    sessions = await db_session.scalars(
        select(Session).where(Session.campaign_id == used_id)
    )
    assert len(list(sessions.all())) == 1


async def test_campaign_endpoints_are_present_in_openapi(make_db_session_dep):
    app = _make_app(make_db_session_dep)
    schema = app.openapi()

    paths = schema["paths"]
    assert "/services/jdr/campaigns" in paths
    assert "/services/jdr/campaigns/{campaign_id}" in paths
    assert "get" in paths["/services/jdr/campaigns"]
    assert "post" in paths["/services/jdr/campaigns"]
    assert "get" in paths["/services/jdr/campaigns/{campaign_id}"]
    assert "patch" in paths["/services/jdr/campaigns/{campaign_id}"]
    assert "delete" in paths["/services/jdr/campaigns/{campaign_id}"]
