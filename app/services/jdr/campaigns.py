"""Campaign context helpers for the JDR service.

Campaigns are a JDR business boundary, but user auth lives in ``app.core``.
This module is the small bridge between both without moving campaign concepts
into core.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedKey
from app.core.models import Profile, User, UserStatus
from app.core.users import get_user
from app.services.jdr.db.models import Campaign, CampaignMember, CampaignRole, Pj
from app.services.jdr.db.repositories import CampaignRepository

DEFAULT_CAMPAIGN_ID = UUID("00000000-0000-0000-0000-000000000001")
DEFAULT_CAMPAIGN_NAME = "Campagne par defaut"


@dataclass(frozen=True, slots=True)
class ActiveCampaignContext:
    id: UUID | None
    name: str | None
    role: CampaignRole | None
    character_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class DefaultCampaignSeedResult:
    campaign_created: bool
    memberships_created: int
    memberships_updated: int
    users_seen: int


def campaign_role_from_profile(profile: Profile) -> CampaignRole:
    if profile == Profile.GM:
        return CampaignRole.MJ
    return CampaignRole.PLAYER


def campaign_role_from_auth(auth: AuthenticatedKey) -> CampaignRole:
    role_value = auth.role.value
    if role_value == Profile.GM.value:
        return CampaignRole.MJ
    return CampaignRole.PLAYER


async def select_default_campaign_owner(session: AsyncSession) -> User | None:
    stmt = (
        select(User)
        .where(User.status == UserStatus.ACTIVE)
        .order_by((User.profile != Profile.GM).asc(), User.created_at.asc(), User.id.asc())
        .limit(1)
    )
    return await session.scalar(stmt)


async def ensure_default_campaign(session: AsyncSession) -> DefaultCampaignSeedResult:
    users = list(
        (
            await session.scalars(
                select(User)
                .where(User.status == UserStatus.ACTIVE)
                .order_by(User.created_at.asc(), User.id.asc())
            )
        ).all()
    )
    if not users:
        return DefaultCampaignSeedResult(
            campaign_created=False,
            memberships_created=0,
            memberships_updated=0,
            users_seen=0,
        )

    repo = CampaignRepository(session)
    campaign = await repo.get_campaign(DEFAULT_CAMPAIGN_ID)
    campaign_created = False
    if campaign is None:
        owner = await select_default_campaign_owner(session)
        if owner is None:
            return DefaultCampaignSeedResult(False, 0, 0, len(users))
        campaign = await repo.create_campaign(
            campaign_id=DEFAULT_CAMPAIGN_ID,
            name=DEFAULT_CAMPAIGN_NAME,
            owner_id=owner.id,
        )
        campaign_created = True

    memberships_created = 0
    memberships_updated = 0
    for user in users:
        existing = await repo.get_membership(user_id=user.id, campaign_id=campaign.id)
        await repo.upsert_membership(
            user_id=user.id,
            campaign_id=campaign.id,
            role=campaign_role_from_profile(user.profile),
        )
        if existing is None:
            memberships_created += 1
        else:
            memberships_updated += 1
        user.default_campaign_id = campaign.id

    await session.flush()
    return DefaultCampaignSeedResult(
        campaign_created=campaign_created,
        memberships_created=memberships_created,
        memberships_updated=memberships_updated,
        users_seen=len(users),
    )


async def ensure_user_membership(
    session: AsyncSession,
    *,
    user: User,
    campaign_id: UUID,
    character_id: UUID | None = None,
) -> CampaignMember:
    repo = CampaignRepository(session)
    row = await repo.upsert_membership(
        user_id=user.id,
        campaign_id=campaign_id,
        role=campaign_role_from_profile(user.profile),
        character_id=character_id,
    )
    user.default_campaign_id = campaign_id
    await session.flush()
    return row


async def sync_membership_role_for_user(
    session: AsyncSession,
    *,
    user: User,
    campaign_id: UUID,
) -> CampaignMember | None:
    repo = CampaignRepository(session)
    row = await repo.get_membership(user_id=user.id, campaign_id=campaign_id)
    if row is None:
        return None
    row.role = campaign_role_from_profile(user.profile)
    await session.flush()
    return row


async def resolve_active_campaign_for_user(
    session: AsyncSession,
    user: User,
) -> ActiveCampaignContext | None:
    repo = CampaignRepository(session)
    resolved: tuple[CampaignMember, Campaign] | None = None

    if user.default_campaign_id is not None:
        resolved = await repo.find_membership_with_campaign(
            user_id=user.id,
            campaign_id=user.default_campaign_id,
        )
    if resolved is None:
        resolved = await repo.first_membership_with_campaign(user_id=user.id)
    if resolved is None:
        return None

    membership, campaign = resolved
    return ActiveCampaignContext(
        id=campaign.id,
        name=campaign.name,
        role=membership.role,
        character_id=membership.character_id,
    )


async def resolve_campaign_for_auth(
    session: AsyncSession,
    auth: AuthenticatedKey,
) -> ActiveCampaignContext | None:
    if auth.source == "web_session" and auth.user_id is not None:
        user = await get_user(session, auth.user_id)
        return await resolve_active_campaign_for_user(session, user)

    campaign = await session.get(Campaign, DEFAULT_CAMPAIGN_ID)
    if campaign is None:
        return ActiveCampaignContext(
            id=None,
            name=None,
            role=campaign_role_from_auth(auth),
            character_id=auth.pj_id,
        )
    return ActiveCampaignContext(
        id=campaign.id,
        name=campaign.name,
        role=campaign_role_from_auth(auth),
        character_id=auth.pj_id,
    )


async def backfill_root_data_to_default_campaign(session: AsyncSession) -> None:
    await session.execute(
        update(Pj)
        .where(Pj.campaign_id.is_(None))
        .values(campaign_id=DEFAULT_CAMPAIGN_ID)
    )
    from app.services.jdr.db.models import Session

    await session.execute(
        update(Session)
        .where(Session.campaign_id.is_(None))
        .values(campaign_id=DEFAULT_CAMPAIGN_ID)
    )
