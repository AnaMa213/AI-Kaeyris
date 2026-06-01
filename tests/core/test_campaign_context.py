"""Campaign context model and resolver tests."""

from app.core.db import Base
from app.core.models import Profile, User
from app.services.jdr.campaign_context import (
    adopt_existing_users_into_default_campaign,
    resolve_active_campaign_for_user,
)
from app.services.jdr.db.models import Campaign, CampaignMember, CampaignRole, Pj, Session
from tests.services.jdr.campaign_fixtures import (
    make_campaign,
    make_membership,
    make_pj,
    make_session,
    make_user,
)


async def test_resolve_active_campaign_prefers_valid_user_default(db_session):
    gm = await make_user(db_session, username="gm", profile=Profile.GM)
    default_campaign = await make_campaign(db_session, owner=gm, name="Default")
    fallback_campaign = await make_campaign(db_session, owner=gm, name="Fallback")
    await make_membership(db_session, user=gm, campaign=fallback_campaign)
    await make_membership(db_session, user=gm, campaign=default_campaign)
    gm.default_campaign_id = default_campaign.id
    await db_session.commit()

    active = await resolve_active_campaign_for_user(db_session, gm)

    assert active is not None
    assert active.id == default_campaign.id
    assert active.name == "Default"
    assert active.role == CampaignRole.GM


async def test_resolve_active_campaign_falls_back_to_first_membership(db_session):
    gm = await make_user(db_session, username="gm", profile=Profile.GM)
    campaign = await make_campaign(db_session, owner=gm, name="Only")
    await make_membership(db_session, user=gm, campaign=campaign)
    gm.default_campaign_id = None
    await db_session.commit()

    active = await resolve_active_campaign_for_user(db_session, gm)

    assert active is not None
    assert active.id == campaign.id


async def test_resolve_active_campaign_returns_none_without_membership(db_session):
    user = await make_user(db_session, username="lonely", profile=Profile.USER)
    await db_session.commit()

    assert await resolve_active_campaign_for_user(db_session, user) is None


async def test_campaign_schema_is_registered_on_metadata():
    assert "jdr_campaigns" in Base.metadata.tables
    assert "jdr_campaign_members" in Base.metadata.tables
    assert "description" in Campaign.__table__.columns
    assert "default_campaign_id" in User.__table__.columns
    assert "campaign_id" in Session.__table__.columns
    assert "campaign_id" in Pj.__table__.columns


async def test_adoption_backfills_users_and_sessions_to_default_campaign(
    db_session,
):
    gm = await make_user(db_session, username="gm", profile=Profile.GM)
    player = await make_user(db_session, username="player", profile=Profile.USER)
    campaign = await make_campaign(db_session, owner=gm)
    session = await make_session(db_session, owner=gm, campaign=campaign)
    session.campaign_id = None
    await db_session.commit()

    adopted = await adopt_existing_users_into_default_campaign(db_session)
    assert adopted is not None
    await db_session.commit()

    gm_membership = await db_session.get(
        CampaignMember,
        {"user_id": gm.id, "campaign_id": adopted.id},
    )
    player_membership = await db_session.get(
        CampaignMember,
        {"user_id": player.id, "campaign_id": adopted.id},
    )

    assert gm.default_campaign_id == adopted.id
    assert player.default_campaign_id == adopted.id
    assert gm_membership is not None
    assert gm_membership.role == CampaignRole.GM
    assert player_membership is not None
    assert player_membership.role == CampaignRole.PLAYER
    assert session.campaign_id == adopted.id


async def test_adoption_backfills_legacy_sessions_without_reassigning_existing_pjs(
    db_session,
):
    gm = await make_user(db_session, username="gm", profile=Profile.GM)
    player = await make_user(db_session, username="player", profile=Profile.USER)
    campaign = await make_campaign(db_session, owner=gm)
    pj = await make_pj(db_session, owner=gm, campaign=None)
    session = await make_session(db_session, owner=gm, campaign=campaign)
    session.campaign_id = None
    await db_session.commit()

    adopted = await adopt_existing_users_into_default_campaign(db_session)
    assert adopted is not None
    await db_session.commit()

    assert gm.default_campaign_id == adopted.id
    assert player.default_campaign_id == adopted.id
    assert pj.campaign_id is None
    assert session.campaign_id == adopted.id
