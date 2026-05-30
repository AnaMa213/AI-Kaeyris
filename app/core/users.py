"""User and web-session helpers.

The public routes stay thin: all password hashing, token hashing, setup
guarding and session validity checks live here so they can be unit-tested.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Profile, User, UserStatus, WebSession
from app.services.jdr.db.models import ApiKey, ApiKeyStatus, CampaignMember, Role

_hasher = PasswordHasher()
_setup_lock = asyncio.Lock()


class DuplicateUserError(Exception):
    """Raised when a username already exists."""


class UserNotFoundError(Exception):
    """Raised when a user id does not exist."""


class SetupClosedError(Exception):
    """Raised when first-run setup is attempted after a user exists."""


class LastActiveGmError(Exception):
    """Raised when a change would remove the last active GM."""


@dataclass(frozen=True, slots=True)
class ValidatedWebSession:
    user: User
    web_session: WebSession


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def normalize_username(username: str) -> str:
    normalized = username.strip().lower()
    if not normalized:
        raise ValueError("Username must not be blank.")
    return normalized


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Password must not be blank.")
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def setup_required(session: AsyncSession) -> bool:
    existing = await session.scalar(select(User.id).limit(1))
    return existing is None


async def get_user(session: AsyncSession, user_id: UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise UserNotFoundError
    return user


async def _username_exists(session: AsyncSession, username: str) -> bool:
    existing = await session.scalar(
        select(User.id).where(User.username == normalize_username(username)).limit(1)
    )
    return existing is not None


def _internal_api_key(username: str) -> ApiKey:
    token = secrets.token_urlsafe(32)
    return ApiKey(
        name=f"web:{username}",
        hash=_hasher.hash(token),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
        pj_id=None,
    )


async def _ensure_gm_api_key(session: AsyncSession, user: User) -> None:
    if user.profile != Profile.GM or user.api_key_id is not None:
        return
    api_key = _internal_api_key(user.username)
    session.add(api_key)
    await session.flush()
    user.api_key_id = api_key.id


async def create_user(
    session: AsyncSession,
    *,
    username: str,
    profile: Profile,
    password: str,
) -> User:
    normalized = normalize_username(username)
    if await _username_exists(session, normalized):
        raise DuplicateUserError

    api_key = _internal_api_key(normalized) if profile == Profile.GM else None
    if api_key is not None:
        session.add(api_key)
        await session.flush()

    now = _utcnow()
    user = User(
        username=normalized,
        profile=profile,
        password_hash=hash_password(password),
        status=UserStatus.ACTIVE,
        api_key_id=api_key.id if api_key is not None else None,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    await session.flush()
    return user


async def create_first_gm(
    session: AsyncSession,
    *,
    username: str,
    password: str,
) -> User:
    async with _setup_lock:
        if not await setup_required(session):
            raise SetupClosedError
        return await create_user(
            session,
            username=username,
            profile=Profile.GM,
            password=password,
        )


async def authenticate_user(
    session: AsyncSession,
    *,
    username: str,
    profile: Profile,
    password: str,
) -> User | None:
    stmt = select(User).where(
        User.username == normalize_username(username),
        User.profile == profile,
        User.status == UserStatus.ACTIVE,
    )
    user = await session.scalar(stmt)
    if user is None or not verify_password(user.password_hash, password):
        return None
    await _ensure_gm_api_key(session, user)
    return user


async def create_web_session(
    session: AsyncSession,
    user: User,
    *,
    ttl_seconds: int,
    user_agent: str | None = None,
    client_ip: str | None = None,
) -> tuple[str, WebSession]:
    token = new_session_token()
    now = _utcnow()
    web_session = WebSession(
        user_id=user.id,
        token_hash=hash_session_token(token),
        created_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
        user_agent=user_agent,
        client_ip=client_ip,
    )
    user.last_login_at = now
    user.updated_at = now
    session.add(web_session)
    await session.flush()
    return token, web_session


async def validate_web_session(
    session: AsyncSession,
    token: str,
) -> ValidatedWebSession | None:
    stmt = (
        select(WebSession, User)
        .join(User, WebSession.user_id == User.id)
        .where(WebSession.token_hash == hash_session_token(token))
        .limit(1)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None

    web_session, user = row
    now = _utcnow()
    if (
        web_session.revoked_at is not None
        or _as_aware_utc(web_session.expires_at) <= now
        or user.status != UserStatus.ACTIVE
    ):
        return None

    web_session.last_seen_at = now
    return ValidatedWebSession(user=user, web_session=web_session)


async def revoke_web_session(session: AsyncSession, token: str) -> None:
    web_session = await session.scalar(
        select(WebSession).where(WebSession.token_hash == hash_session_token(token))
    )
    if web_session is not None and web_session.revoked_at is None:
        web_session.revoked_at = _utcnow()


async def revoke_user_sessions(session: AsyncSession, user_id: UUID) -> None:
    now = _utcnow()
    result = await session.scalars(
        select(WebSession).where(
            WebSession.user_id == user_id,
            WebSession.revoked_at.is_(None),
        )
    )
    for web_session in result.all():
        web_session.revoked_at = now


async def list_users(
    session: AsyncSession,
    *,
    campaign_id: UUID | None = None,
) -> list[User]:
    stmt = select(User).order_by(User.username.asc())
    if campaign_id is not None:
        stmt = stmt.join(CampaignMember, CampaignMember.user_id == User.id).where(
            CampaignMember.campaign_id == campaign_id
        )
    result = await session.scalars(stmt)
    return list(result.all())


async def count_active_gms(session: AsyncSession) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(User).where(
                User.profile == Profile.GM,
                User.status == UserStatus.ACTIVE,
            )
        )
        or 0
    )


async def update_user(
    session: AsyncSession,
    user_id: UUID,
    *,
    profile: Profile | None = None,
    password: str | None = None,
    status: UserStatus | None = None,
) -> User:
    user = await get_user(session, user_id)
    if user.profile == Profile.GM and user.status == UserStatus.ACTIVE:
        would_remove_gm = (
            profile not in (None, Profile.GM)
            or status in (UserStatus.INACTIVE, UserStatus.DELETED)
        )
        if would_remove_gm and await count_active_gms(session) <= 1:
            raise LastActiveGmError

    if profile is not None:
        user.profile = profile
    if password is not None:
        user.password_hash = hash_password(password)
    if status is not None:
        user.status = status
        if status == UserStatus.DELETED and user.deleted_at is None:
            user.deleted_at = _utcnow()
        if status in (UserStatus.INACTIVE, UserStatus.DELETED):
            await revoke_user_sessions(session, user.id)
    await _ensure_gm_api_key(session, user)
    user.updated_at = _utcnow()
    await session.flush()
    return user


async def delete_user(session: AsyncSession, user_id: UUID) -> User:
    user = await get_user(session, user_id)
    if (
        user.profile == Profile.GM
        and user.status == UserStatus.ACTIVE
        and await count_active_gms(session) <= 1
    ):
        raise LastActiveGmError
    user.status = UserStatus.DELETED
    user.deleted_at = _utcnow()
    user.updated_at = user.deleted_at
    await revoke_user_sessions(session, user.id)
    await session.flush()
    return user
