"""make JDR model-settings providers nullable (inherit operator default)

Story 7.2 / BD-22: a per-user ``ModelSettings`` row could not represent
"inherit the operator/env default" for a provider — the columns were
``NOT NULL DEFAULT 'cloud'``. So creating a row to change one category (e.g.
summary -> cloud) silently forced the other category (transcription) to
``cloud``, overriding an operator default of ``local``.

This migration makes ``transcription_provider`` and ``summary_provider``
nullable and drops their server default. ``NULL`` now means "no per-user
override — fall back to the operator/env default" (resolved by the GET endpoint
and the job pipeline). Existing rows keep their stored value untouched.

Revision ID: c1d2e3f4a508
Revises: a7c9d2e4f106
Create Date: 2026-06-27 22:30:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c1d2e3f4a508"
down_revision: Union[str, Sequence[str], None] = "a7c9d2e4f106"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("jdr_model_settings") as batch_op:
        batch_op.alter_column(
            "transcription_provider",
            existing_type=sa.String(length=16),
            nullable=True,
            server_default=None,
        )
        batch_op.alter_column(
            "summary_provider",
            existing_type=sa.String(length=16),
            nullable=True,
            server_default=None,
        )


def downgrade() -> None:
    # Restore NOT NULL + server default. Backfill any NULL (inherit) rows to
    # 'cloud' first so the NOT NULL constraint can be re-applied.
    op.get_bind().execute(
        sa.text(
            "UPDATE jdr_model_settings "
            "SET transcription_provider = 'cloud' "
            "WHERE transcription_provider IS NULL"
        )
    )
    op.get_bind().execute(
        sa.text(
            "UPDATE jdr_model_settings "
            "SET summary_provider = 'cloud' "
            "WHERE summary_provider IS NULL"
        )
    )
    with op.batch_alter_table("jdr_model_settings") as batch_op:
        batch_op.alter_column(
            "transcription_provider",
            existing_type=sa.String(length=16),
            nullable=False,
            server_default="cloud",
        )
        batch_op.alter_column(
            "summary_provider",
            existing_type=sa.String(length=16),
            nullable=False,
            server_default="cloud",
        )
