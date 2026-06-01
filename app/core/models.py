"""Core cross-cutting ORM models.

These tables support browser-facing authentication without replacing the
existing API-key registry used by service automation.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_cls]


class Profile(str, enum.Enum):
    GM = "gm"
    USER = "user"


class UserStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    DELETED = "deleted"


class User(Base):
    """Browser-managed user account.

    GM users get an internal JDR API-key row so existing JDR ownership FKs can
    continue to point to ``jdr_api_keys`` during the transition.
    """

    __tablename__ = "core_users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(
        String(150), unique=True, nullable=False, index=True
    )
    profile: Mapped[Profile] = mapped_column(
        Enum(
            Profile,
            name="core_profile",
            native_enum=False,
            length=16,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[UserStatus] = mapped_column(
        Enum(
            UserStatus,
            name="core_user_status",
            native_enum=False,
            length=16,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=UserStatus.ACTIVE,
    )
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("jdr_api_keys.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    default_campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("jdr_campaigns.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    sessions: Mapped[list[WebSession]] = relationship(
        "WebSession", back_populates="user", cascade="all, delete-orphan"
    )


class WebSession(Base):
    """Opaque browser session stored as a hash of a random cookie token."""

    __tablename__ = "core_web_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("core_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(128), nullable=True)

    user: Mapped[User] = relationship("User", back_populates="sessions")
