"""Small BD-4 fixture helpers for campaign-aware JDR tests."""

from __future__ import annotations

from datetime import UTC, datetime

from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Profile, SystemRole, User, UserStatus
from app.core.users import create_web_session, hash_password
from app.services.jdr.campaign_context import ensure_user_membership
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    Campaign,
    CampaignMember,
    CampaignRole,
    Pj,
    Role,
    Session,
)


async def make_user(
    db: AsyncSession,
    *,
    username: str,
    profile: Profile = Profile.GM,
    system_role: SystemRole | None = None,
    password: str = "password",
) -> User:
    api_key = None
    resolved_system_role = system_role or (
        SystemRole.ADMIN if profile == Profile.GM else SystemRole.USER
    )
    api_key = ApiKey(
        name=f"web:{username}",
        hash=PasswordHasher().hash(f"token-{username}"),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
    )
    db.add(api_key)
    await db.flush()
    user = User(
        username=username,
        system_role=resolved_system_role,
        password_hash=hash_password(password),
        status=UserStatus.ACTIVE,
        api_key_id=api_key.id,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(user)
    await db.flush()
    return user


async def make_campaign(
    db: AsyncSession,
    *,
    owner: User,
    name: str = "Campagne par defaut",
    description: str | None = None,
) -> Campaign:
    campaign = Campaign(
        name=name,
        description=description,
        owner_user_id=owner.id,
        created_at=datetime.now(UTC),
    )
    db.add(campaign)
    await db.flush()
    return campaign


async def make_membership(
    db: AsyncSession,
    *,
    user: User,
    campaign: Campaign,
    role: CampaignRole | None = None,
    character: Pj | None = None,
) -> CampaignMember:
    return await ensure_user_membership(
        db,
        user=user,
        campaign=campaign,
        role=role,
        character_id=character.id if character is not None else None,
    )


async def make_web_session(
    db: AsyncSession,
    *,
    user: User,
) -> str:
    token, _session = await create_web_session(db, user, ttl_seconds=3600)
    await db.commit()
    return token


async def make_pj(
    db: AsyncSession,
    *,
    owner: User,
    campaign: Campaign | None = None,
    name: str = "Aelar",
) -> Pj:
    if owner.api_key_id is None:
        raise ValueError("A PJ fixture requires a GM user with api_key_id.")
    if campaign is None:
        campaign = await make_campaign(db, owner=owner)
    pj = Pj(
        name=name,
        owner_gm_key_id=owner.api_key_id,
        campaign_id=campaign.id,
    )
    db.add(pj)
    await db.flush()
    return pj


async def make_session(
    db: AsyncSession,
    *,
    owner: User,
    campaign: Campaign,
    title: str = "Session test",
    recorded_at: datetime | None = None,
) -> Session:
    if owner.api_key_id is None:
        raise ValueError("A session fixture requires a GM user with api_key_id.")
    session = Session(
        title=title,
        recorded_at=recorded_at or datetime.now(UTC),
        gm_key_id=owner.api_key_id,
        campaign_id=campaign.id,
    )
    db.add(session)
    await db.flush()
    return session
