"""Campaign membership and active-context helpers for the JDR service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedKey
from app.core.models import Profile, User, UserStatus
from app.services.jdr.db.models import (
    Campaign,
    CampaignMember,
    CampaignRole,
    Pj,
    Role,
    Session,
)

DEFAULT_CAMPAIGN_NAME = "Campagne par defaut"


class CampaignContextError(Exception):
    """Base class for campaign-context domain errors."""


class CampaignMembershipError(CampaignContextError):
    """Raised when a campaign membership invariant is violated."""


@dataclass(frozen=True, slots=True)
class ActiveCampaignContext:
    id: UUID
    name: str
    role: CampaignRole
    character_id: UUID | None


@dataclass(frozen=True, slots=True)
class CampaignScope:
    campaign_id: UUID
    role: CampaignRole
    user_id: UUID | None = None
    character_id: UUID | None = None


def campaign_role_for_profile(profile: Profile) -> CampaignRole:
    if profile == Profile.GM:
        return CampaignRole.GM
    return CampaignRole.PLAYER


async def get_default_campaign(session: AsyncSession) -> Campaign | None:
    stmt = select(Campaign).order_by(Campaign.created_at, Campaign.id).limit(1)
    return await session.scalar(stmt)


async def ensure_default_campaign(
    session: AsyncSession,
    *,
    owner_user: User,
    name: str = DEFAULT_CAMPAIGN_NAME,
) -> Campaign:
    campaign = await get_default_campaign(session)
    if campaign is not None:
        return campaign

    campaign = Campaign(
        name=name,
        owner_user_id=owner_user.id,
        created_at=datetime.now(UTC),
    )
    session.add(campaign)
    await session.flush()
    return campaign


async def ensure_user_membership(
    session: AsyncSession,
    *,
    user: User,
    campaign: Campaign,
    role: CampaignRole | None = None,
    character_id: UUID | None = None,
) -> CampaignMember:
    membership = await session.get(
        CampaignMember,
        {"user_id": user.id, "campaign_id": campaign.id},
    )
    if membership is None:
        membership = CampaignMember(
            user_id=user.id,
            campaign_id=campaign.id,
            role=role or campaign_role_for_profile(user.profile),
            character_id=character_id,
            joined_at=datetime.now(UTC),
        )
        session.add(membership)
    else:
        if role is not None:
            membership.role = role
        if character_id is not None:
            membership.character_id = character_id

    if membership.character_id is not None:
        pj_campaign_id = await session.scalar(
            select(Pj.campaign_id).where(Pj.id == membership.character_id)
        )
        if pj_campaign_id != campaign.id:
            raise CampaignMembershipError(
                "A campaign membership character must belong to the same campaign."
            )

    if user.default_campaign_id is None:
        user.default_campaign_id = campaign.id

    await session.flush()
    return membership


async def ensure_default_campaign_for_user(
    session: AsyncSession,
    *,
    owner_user: User,
    user: User,
    role: CampaignRole | None = None,
) -> CampaignMember:
    campaign = await ensure_default_campaign(session, owner_user=owner_user)
    return await ensure_user_membership(
        session,
        user=user,
        campaign=campaign,
        role=role,
    )


async def resolve_active_campaign_for_user(
    session: AsyncSession,
    user: User,
) -> ActiveCampaignContext | None:
    if user.status != UserStatus.ACTIVE:
        return None

    if user.default_campaign_id is not None:
        default_stmt = (
            select(Campaign, CampaignMember)
            .join(CampaignMember, CampaignMember.campaign_id == Campaign.id)
            .where(
                Campaign.id == user.default_campaign_id,
                CampaignMember.user_id == user.id,
            )
            .limit(1)
        )
        default_row = (await session.execute(default_stmt)).first()
        if default_row is not None:
            campaign, membership = default_row
            return ActiveCampaignContext(
                id=campaign.id,
                name=campaign.name,
                role=membership.role,
                character_id=membership.character_id,
            )

    fallback_stmt = (
        select(Campaign, CampaignMember)
        .join(CampaignMember, CampaignMember.campaign_id == Campaign.id)
        .where(CampaignMember.user_id == user.id)
        .order_by(CampaignMember.joined_at, Campaign.id)
        .limit(1)
    )
    fallback_row = (await session.execute(fallback_stmt)).first()
    if fallback_row is None:
        return None

    campaign, membership = fallback_row
    return ActiveCampaignContext(
        id=campaign.id,
        name=campaign.name,
        role=membership.role,
        character_id=membership.character_id,
    )


async def resolve_campaign_scope_for_auth(
    session: AsyncSession,
    auth: AuthenticatedKey,
) -> CampaignScope | None:
    if auth.source == "web_session" and auth.user_id is not None:
        user = await session.get(User, auth.user_id)
        if user is None:
            return None
        active = await resolve_active_campaign_for_user(session, user)
        if active is None:
            return None
        return CampaignScope(
            campaign_id=active.id,
            role=active.role,
            user_id=user.id,
            character_id=active.character_id,
        )

    if auth.role.value == Role.PLAYER.value and auth.pj_id is not None:
        pj = await session.get(Pj, auth.pj_id)
        if pj is None or pj.campaign_id is None:
            return None
        return CampaignScope(
            campaign_id=pj.campaign_id,
            role=CampaignRole.PLAYER,
            character_id=pj.id,
        )

    campaign = await get_default_campaign(session)
    if campaign is None:
        return None
    return CampaignScope(campaign_id=campaign.id, role=CampaignRole.GM)


async def adopt_existing_users_into_default_campaign(
    session: AsyncSession,
) -> Campaign | None:
    owner = await session.scalar(
        select(User)
        .where(User.status == UserStatus.ACTIVE, User.profile == Profile.GM)
        .order_by(User.created_at, User.id)
        .limit(1)
    )
    if owner is None:
        owner = await session.scalar(
            select(User).order_by(User.created_at, User.id).limit(1)
        )
    if owner is None:
        return None

    campaign = await ensure_default_campaign(session, owner_user=owner)
    users = (
        await session.scalars(select(User).order_by(User.created_at, User.id))
    ).all()
    for user in users:
        await ensure_user_membership(
            session,
            user=user,
            campaign=campaign,
            role=campaign_role_for_profile(user.profile),
        )
        if user.default_campaign_id is None:
            user.default_campaign_id = campaign.id
    await session.execute(
        update(Pj).where(Pj.campaign_id.is_(None)).values(campaign_id=campaign.id)
    )
    await session.execute(
        update(Session)
        .where(Session.campaign_id.is_(None))
        .values(campaign_id=campaign.id)
    )
    await session.flush()
    return campaign
