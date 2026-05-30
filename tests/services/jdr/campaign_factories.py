"""Test helpers for campaign-aware JDR fixtures."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Profile, User
from app.core.users import create_user
from app.services.jdr.campaigns import DEFAULT_CAMPAIGN_ID, DEFAULT_CAMPAIGN_NAME
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    Campaign,
    CampaignMember,
    CampaignRole,
    Pj,
    Role,
    Session,
    SessionMode,
    SessionState,
    TranscriptionMode,
)


async def make_user(
    db: AsyncSession,
    *,
    username: str,
    profile: Profile = Profile.USER,
    password: str = "test-password",
) -> User:
    return await create_user(
        db,
        username=username,
        profile=profile,
        password=password,
    )


async def make_campaign(
    db: AsyncSession,
    *,
    owner: User,
    campaign_id: UUID = DEFAULT_CAMPAIGN_ID,
    name: str = DEFAULT_CAMPAIGN_NAME,
) -> Campaign:
    row = Campaign(id=campaign_id, name=name, owner_id=owner.id)
    db.add(row)
    await db.flush()
    return row


async def make_membership(
    db: AsyncSession,
    *,
    user: User,
    campaign: Campaign,
    role: CampaignRole = CampaignRole.PLAYER,
    character: Pj | None = None,
) -> CampaignMember:
    row = CampaignMember(
        user_id=user.id,
        campaign_id=campaign.id,
        role=role,
        character_id=character.id if character is not None else None,
        joined_at=datetime.now(UTC),
    )
    db.add(row)
    user.default_campaign_id = campaign.id
    await db.flush()
    return row


async def make_api_key(
    db: AsyncSession,
    *,
    name: str = "gm-key",
    role: Role = Role.GM,
    pj_id: UUID | None = None,
) -> ApiKey:
    row = ApiKey(
        name=name,
        hash="$argon2id$v=19$m=65536,t=3,p=4$placeholder$placeholder",
        role=role,
        status=ApiKeyStatus.ACTIVE,
        pj_id=pj_id,
    )
    db.add(row)
    await db.flush()
    return row


async def make_pj(
    db: AsyncSession,
    *,
    owner_key: ApiKey,
    campaign: Campaign,
    name: str = "PJ",
) -> Pj:
    row = Pj(name=name, owner_gm_key_id=owner_key.id, campaign_id=campaign.id)
    db.add(row)
    await db.flush()
    return row


async def make_session(
    db: AsyncSession,
    *,
    owner_key: ApiKey,
    campaign: Campaign,
    title: str = "Session",
) -> Session:
    row = Session(
        title=title,
        recorded_at=datetime.now(UTC),
        gm_key_id=owner_key.id,
        campaign_id=campaign.id,
        mode=SessionMode.BATCH,
        state=SessionState.CREATED,
        transcription_mode=TranscriptionMode.DIARISED,
    )
    db.add(row)
    await db.flush()
    return row
