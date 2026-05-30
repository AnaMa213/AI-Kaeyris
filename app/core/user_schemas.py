"""Pydantic schemas for browser user/password authentication."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.models import Profile, UserStatus


class SetupStatusOut(BaseModel):
    required: bool


class SetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=256)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=150)
    profile: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=256)


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=150)
    profile: Profile
    password: str = Field(min_length=1, max_length=256)


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: Profile | None = None
    password: str | None = Field(default=None, min_length=1, max_length=256)
    status: UserStatus | None = None

    @model_validator(mode="after")
    def require_one_change(self) -> "UserUpdate":
        if self.profile is None and self.password is None and self.status is None:
            raise ValueError("At least one user field must be provided.")
        return self


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    profile: Profile
    status: UserStatus
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None = None


class UserListOut(BaseModel):
    items: list[UserOut]


class AuthMeUserOut(BaseModel):
    id: UUID
    username: str


class AuthMeCampaignOut(BaseModel):
    id: UUID
    name: str
    role: str
    character_id: UUID | None = None


class AuthMeOut(BaseModel):
    user: AuthMeUserOut
    active_campaign: AuthMeCampaignOut | None
