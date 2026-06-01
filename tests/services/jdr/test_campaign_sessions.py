"""BD-6 campaign-aware session endpoint tests."""

from collections.abc import Callable
from uuid import UUID, uuid4

import fakeredis
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis import Redis

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.models import Profile
from app.core.redis_client import get_redis
from app.services.jdr.auth_router import router as auth_router
from app.services.jdr.db.models import CampaignRole, Session
from app.services.jdr.router import router as jdr_router
from tests.services.jdr.campaign_fixtures import (
    make_campaign,
    make_membership,
    make_pj,
    make_session,
    make_user,
    make_web_session,
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


async def _client_for(user, db_session, make_db_session_dep) -> AsyncClient:
    token = await make_web_session(db_session, user=user)
    app = _make_app(make_db_session_dep)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    client.cookies.set("session", token)
    return client


async def test_create_session_requires_campaign_id_and_gm_membership(
    db_session,
    make_db_session_dep,
):
    gm = await make_user(db_session, username="gm", profile=Profile.GM)
    player = await make_user(db_session, username="player", profile=Profile.USER)
    campaign = await make_campaign(db_session, owner=gm, name="A")
    await make_membership(db_session, user=gm, campaign=campaign, role=CampaignRole.GM)
    await make_membership(
        db_session,
        user=player,
        campaign=campaign,
        role=CampaignRole.PLAYER,
    )
    gm_client = await _client_for(gm, db_session, make_db_session_dep)
    player_client = await _client_for(player, db_session, make_db_session_dep)

    async with gm_client, player_client:
        missing = await gm_client.post(
            "/services/jdr/sessions",
            json={"title": "No campaign", "recorded_at": "2026-05-31T18:00:00Z"},
        )
        created = await gm_client.post(
            "/services/jdr/sessions",
            json={
                "title": "A1",
                "recorded_at": "2026-05-31T20:00:00+02:00",
                "campaign_id": str(campaign.id),
            },
        )
        forbidden = await player_client.post(
            "/services/jdr/sessions",
            json={
                "title": "Player attempt",
                "recorded_at": "2026-05-31T18:00:00Z",
                "campaign_id": str(campaign.id),
            },
        )

    assert missing.status_code == 422
    assert created.status_code == 201
    row = await db_session.get(Session, UUID(created.json()["id"]))
    assert row is not None
    assert row.campaign_id == campaign.id
    assert created.json()["recorded_at"].endswith(("Z", "+00:00"))
    assert forbidden.status_code == 403


async def test_list_sessions_can_filter_by_campaign(db_session, make_db_session_dep):
    gm = await make_user(db_session, username="gm", profile=Profile.GM)
    campaign_a = await make_campaign(db_session, owner=gm, name="A")
    campaign_b = await make_campaign(db_session, owner=gm, name="B")
    await make_membership(db_session, user=gm, campaign=campaign_a)
    await make_membership(db_session, user=gm, campaign=campaign_b)
    await make_session(db_session, owner=gm, campaign=campaign_a, title="A1")
    await make_session(db_session, owner=gm, campaign=campaign_b, title="B1")
    client = await _client_for(gm, db_session, make_db_session_dep)

    async with client:
        filtered = await client.get(
            f"/services/jdr/sessions?campaign_id={campaign_a.id}"
        )
        unfiltered = await client.get("/services/jdr/sessions")
        invalid = await client.get("/services/jdr/sessions?campaign_id=not-a-uuid")

    assert filtered.status_code == 200
    assert [item["title"] for item in filtered.json()["items"]] == ["A1"]
    assert unfiltered.status_code == 200
    assert {item["title"] for item in unfiltered.json()["items"]} == {"A1", "B1"}
    assert invalid.status_code == 422


async def test_list_sessions_rejects_non_member_campaign(
    db_session,
    make_db_session_dep,
):
    gm = await make_user(db_session, username="gm", profile=Profile.GM)
    other = await make_user(db_session, username="other", profile=Profile.GM)
    campaign = await make_campaign(db_session, owner=other, name="Other")
    await make_membership(db_session, user=other, campaign=campaign)
    client = await _client_for(gm, db_session, make_db_session_dep)

    async with client:
        response = await client.get(f"/services/jdr/sessions?campaign_id={campaign.id}")

    assert response.status_code == 403


async def test_session_detail_rejects_cross_campaign_member(
    db_session,
    make_db_session_dep,
):
    gm = await make_user(db_session, username="gm", profile=Profile.GM)
    other = await make_user(db_session, username="other", profile=Profile.GM)
    campaign = await make_campaign(db_session, owner=other, name="Other")
    await make_membership(db_session, user=other, campaign=campaign)
    session = await make_session(db_session, owner=other, campaign=campaign)
    client = await _client_for(gm, db_session, make_db_session_dep)

    async with client:
        forbidden = await client.get(f"/services/jdr/sessions/{session.id}")
        missing = await client.get(f"/services/jdr/sessions/{uuid4()}")

    assert forbidden.status_code == 403
    assert missing.status_code == 404


async def test_pjs_remain_global_to_gm_for_bd6_public_endpoints(
    db_session,
    make_db_session_dep,
):
    gm = await make_user(db_session, username="gm", profile=Profile.GM)
    campaign_a = await make_campaign(db_session, owner=gm, name="A")
    campaign_b = await make_campaign(db_session, owner=gm, name="B")
    await make_membership(db_session, user=gm, campaign=campaign_a)
    await make_membership(db_session, user=gm, campaign=campaign_b)
    await make_pj(db_session, owner=gm, campaign=campaign_b, name="Existing")
    gm.default_campaign_id = campaign_a.id
    client = await _client_for(gm, db_session, make_db_session_dep)

    async with client:
        created = await client.post("/services/jdr/pjs", json={"name": "New"})
        listed = await client.get("/services/jdr/pjs")

    assert created.status_code == 201
    assert listed.status_code == 200
    assert {item["name"] for item in listed.json()["items"]} == {"Existing", "New"}
    assert (await db_session.get(Session, uuid4())) is None


async def test_session_campaign_id_is_present_in_openapi(make_db_session_dep):
    schema = _make_app(make_db_session_dep).openapi()

    post_schema = schema["paths"]["/services/jdr/sessions"]["post"]
    get_params = schema["paths"]["/services/jdr/sessions"]["get"]["parameters"]

    assert any(param["name"] == "campaign_id" for param in get_params)
    body_ref = post_schema["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    component_name = body_ref.rsplit("/", 1)[-1]
    session_schema = schema["components"]["schemas"][component_name]
    assert "campaign_id" in session_schema["required"]
