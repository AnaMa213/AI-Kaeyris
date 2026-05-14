"""add campaign_context to jdr_sessions

Revision ID: 8b2f1c4d9e07
Revises: 25a849ed7115
Create Date: 2026-05-13 21:00:00.000000

Adds an optional ``campaign_context`` column to ``jdr_sessions``. The
MJ can attach a "campaign bible" to a session that gets injected as a
global steering prompt into the narrative and elements LLM jobs (Lot 4c).

Nullable retroactively: existing rows get NULL, which means the prompts
behave exactly as before (no context block prepended).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "8b2f1c4d9e07"
down_revision: Union[str, Sequence[str], None] = "25a849ed7115"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jdr_sessions",
        sa.Column("campaign_context", sa.String(length=8000), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jdr_sessions", "campaign_context")
