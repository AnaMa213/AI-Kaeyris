"""Pydantic schemas for browser user/password authentication."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    model_validator,
)

from app.core.datetime_serialization import serialize_datetime_utc
from app.core.models import SystemRole, UserStatus


class UserSchema(BaseModel):
    """Base schema for browser-auth JSON output conventions."""

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_datetimes(self, value):
        if isinstance(value, datetime):
            return serialize_datetime_utc(value)
        return value


class SetupStatusOut(UserSchema):
    required: bool


class SetupRequest(UserSchema):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=256)


class LoginRequest(UserSchema):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=256)


class UserCreate(UserSchema):
    username: str = Field(min_length=1, max_length=150)
    system_role: SystemRole = Field(
        default=SystemRole.USER,
        validation_alias=AliasChoices("system_role", "profile"),
    )
    password: str = Field(min_length=1, max_length=256)


class UserUpdate(UserSchema):
    system_role: SystemRole | None = Field(
        default=None,
        validation_alias=AliasChoices("system_role", "profile"),
    )
    password: str | None = Field(default=None, min_length=1, max_length=256)
    status: UserStatus | None = None

    @model_validator(mode="after")
    def require_one_change(self) -> "UserUpdate":
        if self.system_role is None and self.password is None and self.status is None:
            raise ValueError("At least one user field must be provided.")
        return self


class UserOut(UserSchema):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    system_role: SystemRole
    status: UserStatus
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None = None


class UserListOut(UserSchema):
    items: list[UserOut]


class AuthMeUserOut(UserSchema):
    id: UUID
    username: str
    system_role: SystemRole


class AuthMeCampaignOut(UserSchema):
    id: UUID
    name: str
    role: str
    character_id: UUID | None = None


class AuthMeOut(UserSchema):
    user: AuthMeUserOut
    active_campaign: AuthMeCampaignOut | None = None
