"""identity refactor and PJ campaign scoping for BD-7

Revision ID: b8e4c1d2f3a9
Revises: a7f3d2c9b8e4
Create Date: 2026-06-01 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8e4c1d2f3a9"
down_revision: Union[str, Sequence[str], None] = "a7f3d2c9b8e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("core_users") as batch_op:
        batch_op.add_column(
            sa.Column("system_role", sa.String(length=16), nullable=True)
        )
    op.execute(
        "UPDATE core_users SET system_role = CASE "
        "WHEN profile = 'gm' THEN 'admin' ELSE 'user' END"
    )
    with op.batch_alter_table("core_users") as batch_op:
        batch_op.alter_column("system_role", existing_type=sa.String(16), nullable=False)
        batch_op.drop_column("profile")

    op.execute("UPDATE jdr_campaign_members SET role = 'pj' WHERE role = 'player'")

    with op.batch_alter_table("jdr_pjs") as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_jdr_pjs_user_id_core_users",
            "core_users",
            ["user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.alter_column("campaign_id", existing_type=sa.Uuid(), nullable=False)
        batch_op.create_index("ix_jdr_pjs_user_id", ["user_id"])


def downgrade() -> None:
    with op.batch_alter_table("jdr_pjs") as batch_op:
        batch_op.drop_index("ix_jdr_pjs_user_id")
        batch_op.drop_constraint("fk_jdr_pjs_user_id_core_users", type_="foreignkey")
        batch_op.drop_column("user_id")
        batch_op.alter_column("campaign_id", existing_type=sa.Uuid(), nullable=True)

    op.execute("UPDATE jdr_campaign_members SET role = 'player' WHERE role = 'pj'")

    with op.batch_alter_table("core_users") as batch_op:
        batch_op.add_column(sa.Column("profile", sa.String(length=16), nullable=True))
    op.execute(
        "UPDATE core_users SET profile = CASE "
        "WHEN system_role = 'admin' THEN 'gm' ELSE 'user' END"
    )
    with op.batch_alter_table("core_users") as batch_op:
        batch_op.alter_column("profile", existing_type=sa.String(16), nullable=False)
        batch_op.drop_column("system_role")
