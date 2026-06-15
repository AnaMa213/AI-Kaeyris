"""add local model path columns to JDR AI model settings

BD-18 model config (Story 6.3 / FR-23): adds per-category custom local
model path columns, used when the corresponding provider is "local".

Revision ID: c5e9f7b3d024
Revises: b4d8e6a2c913
Create Date: 2026-06-15 18:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c5e9f7b3d024"
down_revision: Union[str, Sequence[str], None] = "b4d8e6a2c913"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jdr_model_settings",
        sa.Column("transcription_local_path", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "jdr_model_settings",
        sa.Column("summary_local_path", sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jdr_model_settings", "summary_local_path")
    op.drop_column("jdr_model_settings", "transcription_local_path")
