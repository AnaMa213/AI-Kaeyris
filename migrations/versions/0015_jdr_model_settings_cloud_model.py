"""add cloud model + DeepInfra key columns to JDR AI model settings

BD-18 model config (Story 6.4 / FR-24): adds per-category DeepInfra cloud
model id columns (used when the corresponding provider is "cloud") plus an
account-level write-only DeepInfra API key column (never serialized back;
only the derived ``deepinfra_api_key_set`` boolean is exposed).

Revision ID: d6f0a8c4e135
Revises: c5e9f7b3d024
Create Date: 2026-06-15 19:30:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d6f0a8c4e135"
down_revision: Union[str, Sequence[str], None] = "c5e9f7b3d024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jdr_model_settings",
        sa.Column("transcription_cloud_model", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "jdr_model_settings",
        sa.Column("summary_cloud_model", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "jdr_model_settings",
        sa.Column("deepinfra_api_key", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jdr_model_settings", "deepinfra_api_key")
    op.drop_column("jdr_model_settings", "summary_cloud_model")
    op.drop_column("jdr_model_settings", "transcription_cloud_model")
