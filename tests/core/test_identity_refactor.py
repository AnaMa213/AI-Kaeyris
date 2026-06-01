"""BD-7 identity model and public schema guardrails."""

from app.core.models import SystemRole
from app.core.user_schemas import UserOut
from app.services.jdr.db.models import CampaignRole
from app.services.jdr.schemas import CampaignOut, PjOut
from tests.services.jdr.campaign_fixtures import (
    make_campaign,
    make_membership,
    make_pj,
    make_user,
)


async def test_public_user_schema_exposes_system_role_without_profile(db_session):
    user = await make_user(
        db_session,
        username="admin",
        system_role=SystemRole.ADMIN,
    )

    body = UserOut.model_validate(user).model_dump(mode="json")

    assert body["system_role"] == "admin"
    assert "profile" not in body


async def test_campaign_and_pj_public_contract_uses_gm_pj_and_scoped_pj(
    db_session,
):
    owner = await make_user(db_session, username="gm", system_role=SystemRole.USER)
    player = await make_user(db_session, username="alice", system_role=SystemRole.USER)
    campaign = await make_campaign(db_session, owner=owner)
    await make_membership(
        db_session,
        user=player,
        campaign=campaign,
        role=CampaignRole.PJ,
    )
    pj = await make_pj(db_session, owner=owner, campaign=campaign)
    pj.user_id = player.id
    await db_session.flush()

    campaign_body = CampaignOut(
        id=campaign.id,
        name=campaign.name,
        description=campaign.description,
        role=CampaignRole.PJ.value,
        session_count=0,
        last_session_at=None,
        created_at=campaign.created_at,
    ).model_dump(mode="json")
    pj_body = PjOut.model_validate(pj).model_dump(mode="json")

    assert campaign_body["role"] == "pj"
    assert campaign_body["role"] != "player"
    assert pj_body["campaign_id"] == str(campaign.id)
    assert pj_body["user_id"] == str(player.id)
