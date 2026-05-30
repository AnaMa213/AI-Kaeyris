"""ORM and repository checks for campaign memberships."""

from uuid import uuid4

import pytest

from app.core.models import Profile
from app.services.jdr.db.models import CampaignMember, CampaignRole
from app.services.jdr.db.repositories import CampaignRepository
from tests.services.jdr.campaign_factories import (
    make_api_key,
    make_campaign,
    make_membership,
    make_pj,
    make_session,
    make_user,
)


async def test_campaign_membership_relates_user_campaign_and_character(db_session):
    gm = await make_user(db_session, username="gm", profile=Profile.GM)
    campaign = await make_campaign(db_session, owner=gm)
    api_key = await make_api_key(db_session)
    pj = await make_pj(db_session, owner_key=api_key, campaign=campaign)
    membership = await make_membership(
        db_session,
        user=gm,
        campaign=campaign,
        role=CampaignRole.MJ,
        character=pj,
    )
    await db_session.refresh(membership, ["user", "campaign", "character"])

    assert membership.user_id == gm.id
    assert membership.campaign_id == campaign.id
    assert membership.character_id == pj.id
    assert membership.user.default_campaign_id == campaign.id
    assert membership.campaign.name == campaign.name
    assert membership.character.name == pj.name


async def test_membership_upsert_rejects_character_from_another_campaign(db_session):
    gm = await make_user(db_session, username="gm", profile=Profile.GM)
    user = await make_user(db_session, username="player")
    campaign_a = await make_campaign(db_session, owner=gm, campaign_id=uuid4(), name="A")
    campaign_b = await make_campaign(db_session, owner=gm, campaign_id=uuid4(), name="B")
    api_key = await make_api_key(db_session)
    foreign_pj = await make_pj(db_session, owner_key=api_key, campaign=campaign_b)

    with pytest.raises(ValueError, match="same campaign"):
        await CampaignRepository(db_session).upsert_membership(
            user_id=user.id,
            campaign_id=campaign_a.id,
            role=CampaignRole.PLAYER,
            character_id=foreign_pj.id,
        )


async def test_session_and_pj_store_campaign_id(db_session):
    gm = await make_user(db_session, username="gm", profile=Profile.GM)
    campaign = await make_campaign(db_session, owner=gm)
    api_key = await make_api_key(db_session)

    session = await make_session(db_session, owner_key=api_key, campaign=campaign)
    pj = await make_pj(db_session, owner_key=api_key, campaign=campaign)

    assert session.campaign_id == campaign.id
    assert pj.campaign_id == campaign.id


async def test_campaign_member_composite_primary_key(db_session):
    gm = await make_user(db_session, username="gm", profile=Profile.GM)
    campaign = await make_campaign(db_session, owner=gm)

    await make_membership(db_session, user=gm, campaign=campaign, role=CampaignRole.MJ)
    duplicate = CampaignMember(
        user_id=gm.id,
        campaign_id=campaign.id,
        role=CampaignRole.MJ,
    )
    db_session.add(duplicate)

    with pytest.raises(Exception):
        await db_session.flush()
