"""add per-user JDR AI model settings

BD-18 model config: store account-level provider choices for transcription
and summary. This migration intentionally stores only provider selection;
local paths, cloud keys, and model registries are follow-up contracts.

Revision ID: b4d8e6a2c913
Revises: a1c2e3f4b5d6
Create Date: 2026-06-15 17:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b4d8e6a2c913"
down_revision: Union[str, Sequence[str], None] = "a1c2e3f4b5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "jdr_model_settings",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "transcription_provider",
            sa.String(length=16),
            server_default="cloud",
            nullable=False,
        ),
        sa.Column(
            "summary_provider",
            sa.String(length=16),
            server_default="cloud",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["core_users.id"],
            name="fk_jdr_model_settings_user_id_core_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("jdr_model_settings")
