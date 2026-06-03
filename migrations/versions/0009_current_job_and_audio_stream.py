"""current job pointer for BD-8

Revision ID: c2f4a8b9d0e1
Revises: b8e4c1d2f3a9
Create Date: 2026-06-03 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c2f4a8b9d0e1"
down_revision: Union[str, Sequence[str], None] = "b8e4c1d2f3a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("jdr_sessions") as batch_op:
        batch_op.add_column(sa.Column("current_job_id", sa.String(length=64), nullable=True))
        batch_op.create_foreign_key(
            "fk_jdr_sessions_current_job_id_jdr_jobs",
            "jdr_jobs",
            ["current_job_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_jdr_sessions_current_job_id",
            ["current_job_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("jdr_sessions") as batch_op:
        batch_op.drop_index("ix_jdr_sessions_current_job_id")
        batch_op.drop_constraint(
            "fk_jdr_sessions_current_job_id_jdr_jobs",
            type_="foreignkey",
        )
        batch_op.drop_column("current_job_id")
