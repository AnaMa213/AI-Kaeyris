"""Campaign membership repository and invariant tests."""

import pytest
from uuid import UUID
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.models import Profile
from app.services.jdr.campaign_context import (
    CampaignMembershipError,
    campaign_role_for_profile,
    ensure_user_membership,
)
from app.services.jdr.auth_router import router as auth_router
from app.services.jdr.db.models import Campaign, CampaignMember, CampaignRole
from app.services.jdr.db.repositories import CampaignRepository
from tests.services.jdr.campaign_fixtures import (
    make_campaign,
    make_membership,
    make_pj,
    make_user,
    make_web_session,
)


def _make_app(make_db_session_dep) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    return app


async def test_campaign_repository_lists_campaign_members(db_session):
    gm = await make_user(db_session, username="gm", profile=Profile.GM)
    player = await make_user(db_session, username="player", profile=Profile.USER)
    campaign = await make_campaign(db_session, owner=gm)
    await make_membership(db_session, user=gm, campaign=campaign)
    await make_membership(db_session, user=player, campaign=campaign)
    await db_session.commit()

    user_ids = await CampaignRepository(db_session).list_campaign_user_ids(campaign.id)

    assert set(user_ids) == {gm.id, player.id}


async def test_profile_to_campaign_role_mapping_is_stable():
    assert campaign_role_for_profile(Profile.GM) == CampaignRole.GM
    assert campaign_role_for_profile(Profile.USER) == CampaignRole.PLAYER


async def test_membership_rejects_character_from_another_campaign(db_session):
    gm = await make_user(db_session, username="gm", profile=Profile.GM)
    player = await make_user(db_session, username="player", profile=Profile.USER)
    campaign_a = await make_campaign(db_session, owner=gm, name="A")
    campaign_b = await make_campaign(db_session, owner=gm, name="B")
    pj = await make_pj(db_session, owner=gm, campaign=campaign_b)

    with pytest.raises(CampaignMembershipError):
        await ensure_user_membership(
            db_session,
            user=player,
            campaign=campaign_a,
            role=CampaignRole.PLAYER,
            character_id=pj.id,
        )


async def test_membership_is_idempotent(db_session):
    gm = await make_user(db_session, username="gm", profile=Profile.GM)
    campaign = await make_campaign(db_session, owner=gm)

    first = await make_membership(db_session, user=gm, campaign=campaign)
    second = await make_membership(db_session, user=gm, campaign=campaign)

    assert first.user_id == second.user_id
    assert first.campaign_id == second.campaign_id


async def test_post_users_creates_memberships_for_gm_and_player_profiles(
    db_session,
    make_db_session_dep,
):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        setup = await client.post(
            "/services/jdr/auth/setup",
            json={"username": "admin", "password": "admin-password"},
        )
        assert setup.status_code == 201
        player = await client.post(
            "/services/jdr/users",
            json={"username": "alice", "profile": "user", "password": "secret"},
        )
        gm = await client.post(
            "/services/jdr/users",
            json={"username": "bob", "profile": "gm", "password": "secret"},
        )

    assert player.status_code == 201
    assert gm.status_code == 201
    campaign = await db_session.scalar(select(Campaign))
    assert campaign is not None
    player_membership = await db_session.get(
        CampaignMember,
        {"user_id": UUID(player.json()["id"]), "campaign_id": campaign.id},
    )
    gm_membership = await db_session.get(
        CampaignMember,
        {"user_id": UUID(gm.json()["id"]), "campaign_id": campaign.id},
    )
    assert player_membership is not None
    assert player_membership.role == CampaignRole.PLAYER
    assert gm_membership is not None
    assert gm_membership.role == CampaignRole.GM


async def test_post_users_falls_back_to_default_campaign_when_creator_has_no_scope(
    db_session,
    make_db_session_dep,
):
    gm = await make_user(db_session, username="admin", profile=Profile.GM)
    token = await make_web_session(db_session, user=gm)
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("session", token)
        response = await client.post(
            "/services/jdr/users",
            json={"username": "alice", "profile": "user", "password": "secret"},
        )

    assert response.status_code == 201
    campaign = await db_session.scalar(select(Campaign))
    assert campaign is not None
    membership = await db_session.get(
        CampaignMember,
        {"user_id": UUID(response.json()["id"]), "campaign_id": campaign.id},
    )
    assert membership is not None
    assert membership.role == CampaignRole.PLAYER


async def test_delete_keeps_membership_rows_while_blocking_login(
    db_session,
    make_db_session_dep,
):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/services/jdr/auth/setup",
            json={"username": "admin", "password": "admin-password"},
        )
        created = await client.post(
            "/services/jdr/users",
            json={"username": "alice", "profile": "user", "password": "secret"},
        )
        user_id = UUID(created.json()["id"])
        deleted = await client.delete(f"/services/jdr/users/{user_id}")
        login = await client.post(
            "/services/jdr/auth/login",
            json={"username": "alice", "profile": "user", "password": "secret"},
        )

    assert deleted.status_code == 204
    assert login.status_code == 401
    campaign = await db_session.scalar(select(Campaign))
    membership = await db_session.get(
        CampaignMember,
        {"user_id": user_id, "campaign_id": campaign.id},
    )
    assert membership is not None


async def test_get_users_lists_only_active_campaign_members(
    db_session,
    make_db_session_dep,
):
    admin = await make_user(db_session, username="admin", profile=Profile.GM)
    campaign_a = await make_campaign(db_session, owner=admin, name="A")
    campaign_b = await make_campaign(db_session, owner=admin, name="B")
    await make_membership(db_session, user=admin, campaign=campaign_a)
    other = await make_user(db_session, username="other", profile=Profile.USER)
    await make_membership(db_session, user=other, campaign=campaign_b)
    token = await make_web_session(db_session, user=admin)
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("session", token)
        response = await client.get("/services/jdr/users")

    assert response.status_code == 200
    assert [item["username"] for item in response.json()["items"]] == ["admin"]
