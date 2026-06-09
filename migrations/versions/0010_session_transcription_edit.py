"""persist edited session transcription markdown for BD-13

Revision ID: d9a7c3e5f1b2
Revises: c2f4a8b9d0e1
Create Date: 2026-06-09 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d9a7c3e5f1b2"
down_revision: Union[str, Sequence[str], None] = "c2f4a8b9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("jdr_sessions") as batch_op:
        batch_op.add_column(sa.Column("edited_transcript_md", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("jdr_sessions") as batch_op:
        batch_op.drop_column("edited_transcript_md")
