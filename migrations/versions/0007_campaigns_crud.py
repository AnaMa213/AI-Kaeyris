"""add campaign description for BD-6

Revision ID: a7f3d2c9b8e4
Revises: d4c9b8a7e6f1
Create Date: 2026-06-01 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7f3d2c9b8e4"
down_revision: Union[str, Sequence[str], None] = "d4c9b8a7e6f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("jdr_campaigns") as batch_op:
        batch_op.add_column(
            sa.Column("description", sa.String(length=4000), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("jdr_campaigns") as batch_op:
        batch_op.drop_column("description")
