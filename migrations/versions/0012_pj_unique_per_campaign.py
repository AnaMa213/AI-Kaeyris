"""scope PJ name uniqueness to the campaign, not the MJ

BD fix: ``jdr_pjs`` had a UNIQUE (owner_gm_key_id, name) constraint
(``owner_name``) predating BD-7 campaign scoping, so the same PJ name could
not be reused across two campaigns of the same MJ. PJs are scoped per campaign
now, so the uniqueness key becomes (campaign_id, name).

Revision ID: a1c2e3f4b5d6
Revises: e3f1a9b27c40
Create Date: 2026-06-12 00:00:01.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "a1c2e3f4b5d6"
down_revision: Union[str, Sequence[str], None] = "e3f1a9b27c40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("jdr_pjs") as batch_op:
        batch_op.drop_constraint("owner_name", type_="unique")
        batch_op.create_unique_constraint(
            "uq_jdr_pjs_campaign_name", ["campaign_id", "name"]
        )


def downgrade() -> None:
    with op.batch_alter_table("jdr_pjs") as batch_op:
        batch_op.drop_constraint("uq_jdr_pjs_campaign_name", type_="unique")
        batch_op.create_unique_constraint("owner_name", ["owner_gm_key_id", "name"])
