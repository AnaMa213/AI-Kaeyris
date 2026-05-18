"""non_diarised mode — transcription_mode on sessions + chunks + session_players

Revision ID: a4d7e6b91f23
Revises: 8b2f1c4d9e07
Create Date: 2026-05-18 12:00:00.000000

Sous-jalon 5.5 — feature 002-non-diarised-mode.

Adds a `transcription_mode` enum column to `jdr_sessions` (default 'diarised',
NOT NULL) so a MJ can opt a session out of diarisation at creation time.
Existing sessions automatically pick up the default value via the
``server_default``, preserving Jalon 5 behaviour.

Creates `jdr_chunks` to store the chunked transcription of `non_diarised`
sessions, with an inline `summary_text` column populated by the map phase
of the `summary` job (data-model.md §3 of feature 002).

Creates `jdr_session_players` to declare the PJ present at a `non_diarised`
session (equivalent of `jdr_session_pj_mappings` minus the speaker_label,
data-model.md §4 of feature 002).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a4d7e6b91f23"
down_revision: Union[str, Sequence[str], None] = "8b2f1c4d9e07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) jdr_sessions.transcription_mode — NOT NULL with server_default for
    #    backward compatibility with existing rows.
    op.add_column(
        "jdr_sessions",
        sa.Column(
            "transcription_mode",
            sa.String(length=16),
            nullable=False,
            server_default="diarised",
        ),
    )

    # 2) jdr_chunks
    op.create_table(
        "jdr_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("ordre", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["jdr_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id", "ordre", name="uq_jdr_chunks_session_ordre"
        ),
    )
    op.create_index(
        "ix_jdr_chunks_session_id", "jdr_chunks", ["session_id"]
    )

    # 3) jdr_session_players
    op.create_table(
        "jdr_session_players",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("pj_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["jdr_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pj_id"],
            ["jdr_pjs.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("session_id", "pj_id"),
    )
    op.create_index(
        "ix_jdr_session_players_pj_id", "jdr_session_players", ["pj_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_jdr_session_players_pj_id", table_name="jdr_session_players"
    )
    op.drop_table("jdr_session_players")
    op.drop_index("ix_jdr_chunks_session_id", table_name="jdr_chunks")
    op.drop_table("jdr_chunks")
    op.drop_column("jdr_sessions", "transcription_mode")
