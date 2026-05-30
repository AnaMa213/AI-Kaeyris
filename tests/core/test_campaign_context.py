"""Campaign context primitives."""

from uuid import uuid4

from app.core.models import Profile
from app.services.jdr.campaigns import (
    DEFAULT_CAMPAIGN_ID,
    DEFAULT_CAMPAIGN_NAME,
    campaign_role_from_profile,
    ensure_default_campaign,
    resolve_active_campaign_for_user,
)
from app.services.jdr.db.models import CampaignRole
from tests.services.jdr.campaign_factories import (
    make_campaign,
    make_membership,
    make_user,
)


def test_default_campaign_id_and_profile_role_mapping_are_stable():
    assert str(DEFAULT_CAMPAIGN_ID) == "00000000-0000-0000-0000-000000000001"
    assert campaign_role_from_profile(Profile.GM) == CampaignRole.MJ
    assert campaign_role_from_profile(Profile.USER) == CampaignRole.PLAYER


async def test_resolve_active_campaign_returns_none_without_membership(db_session):
    user = await make_user(db_session, username="player")

    assert await resolve_active_campaign_for_user(db_session, user) is None


async def test_resolve_active_campaign_uses_valid_default_then_fallback(db_session):
    owner = await make_user(db_session, username="gm", profile=Profile.GM)
    user = await make_user(db_session, username="player")
    first = await make_campaign(db_session, owner=owner, campaign_id=uuid4(), name="A")
    second = await make_campaign(db_session, owner=owner, campaign_id=uuid4(), name="B")
    await make_membership(db_session, user=user, campaign=first)
    await make_membership(db_session, user=user, campaign=second, role=CampaignRole.MJ)

    user.default_campaign_id = second.id
    resolved = await resolve_active_campaign_for_user(db_session, user)
    assert resolved is not None
    assert resolved.id == second.id
    assert resolved.role == CampaignRole.MJ

    user.default_campaign_id = uuid4()
    resolved = await resolve_active_campaign_for_user(db_session, user)
    assert resolved is not None
    assert resolved.id == first.id


async def test_ensure_default_campaign_is_idempotent_and_prefers_first_gm(db_session):
    player = await make_user(db_session, username="player")
    gm = await make_user(db_session, username="gm", profile=Profile.GM)

    first = await ensure_default_campaign(db_session)
    second = await ensure_default_campaign(db_session)

    assert first.campaign_created is True
    assert first.memberships_created == 2
    assert second.campaign_created is False
    assert second.memberships_created == 0
    assert second.memberships_updated == 2

    resolved_gm = await resolve_active_campaign_for_user(db_session, gm)
    resolved_player = await resolve_active_campaign_for_user(db_session, player)
    assert resolved_gm is not None
    assert resolved_gm.id == DEFAULT_CAMPAIGN_ID
    assert resolved_gm.name == DEFAULT_CAMPAIGN_NAME
    assert resolved_gm.role == CampaignRole.MJ
    assert resolved_player is not None
    assert resolved_player.role == CampaignRole.PLAYER
