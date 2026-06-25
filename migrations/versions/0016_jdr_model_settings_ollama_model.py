"""add Ollama model column to JDR AI model settings

BD-19 model routing (Story 6.5): stores the summary LLM model name used
when a GM selects the Ollama HTTP provider.

Revision ID: e4b1c9f2a037
Revises: d6f0a8c4e135
Create Date: 2026-06-16 12:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e4b1c9f2a037"
down_revision: Union[str, Sequence[str], None] = "d6f0a8c4e135"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jdr_model_settings",
        sa.Column("ollama_model", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jdr_model_settings", "ollama_model")
