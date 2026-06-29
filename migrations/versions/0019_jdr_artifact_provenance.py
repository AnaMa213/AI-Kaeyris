"""add manual-edit provenance to JDR artifacts

BD-24 / Epic 8 Story 8.1: artefacts become MJ-editable. We record whether an
artefact was hand-edited since its last AI generation, when, and by whom —
without touching ``model_used``/``generated_at`` (the immutable record of the
last generation).

Scope note: the Epic 8 plan groups this with the elements free-form reshape
(BD-26). That data migration is intentionally deferred to the US2 batch so the
elements read endpoint and its new projection ship together; transforming the
stored shape here, ahead of that code, would break ``GET .../elements``. This
migration is therefore purely additive (three nullable/defaulted columns).

Revision ID: d3e5f7a9b210
Revises: c1d2e3f4a508
Create Date: 2026-06-29 21:30:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d3e5f7a9b210"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4a508"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("jdr_artifacts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_edited",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("edited_by", sa.String(length=64), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("jdr_artifacts") as batch_op:
        batch_op.drop_column("edited_by")
        batch_op.drop_column("edited_at")
        batch_op.drop_column("is_edited")
