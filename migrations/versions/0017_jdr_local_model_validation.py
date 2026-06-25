"""add local model validation proofs

BD-20 local model validation: stores short-lived proof hashes and links
accepted local proofs to per-GM model settings.

Revision ID: a7c9d2e4f106
Revises: e4b1c9f2a037
Create Date: 2026-06-16 12:30:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a7c9d2e4f106"
down_revision: Union[str, Sequence[str], None] = "e4b1c9f2a037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jdr_model_settings",
        sa.Column(
            "transcription_local_validation_hash",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.add_column(
        "jdr_model_settings",
        sa.Column(
            "summary_local_validation_hash",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.create_table(
        "jdr_local_model_validations",
        sa.Column("validation_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("model_path", sa.String(length=1024), nullable=False),
        sa.Column("path_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="succeeded",
            nullable=False,
        ),
        sa.Column("runtime", sa.String(length=64), nullable=False),
        sa.Column("model_format", sa.String(length=64), nullable=False),
        sa.Column("message", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["core_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("validation_hash"),
    )
    op.create_index(
        "ix_jdr_local_model_validations_user_id",
        "jdr_local_model_validations",
        ["user_id"],
    )
    op.create_index(
        "ix_jdr_local_model_validations_category",
        "jdr_local_model_validations",
        ["category"],
    )
    op.create_index(
        "ix_jdr_local_model_validations_path_hash",
        "jdr_local_model_validations",
        ["path_hash"],
    )
    op.create_index(
        "ix_jdr_local_model_validations_expires_at",
        "jdr_local_model_validations",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_jdr_local_model_validations_expires_at",
        table_name="jdr_local_model_validations",
    )
    op.drop_index(
        "ix_jdr_local_model_validations_path_hash",
        table_name="jdr_local_model_validations",
    )
    op.drop_index(
        "ix_jdr_local_model_validations_category",
        table_name="jdr_local_model_validations",
    )
    op.drop_index(
        "ix_jdr_local_model_validations_user_id",
        table_name="jdr_local_model_validations",
    )
    op.drop_table("jdr_local_model_validations")
    op.drop_column("jdr_model_settings", "summary_local_validation_hash")
    op.drop_column("jdr_model_settings", "transcription_local_validation_hash")
