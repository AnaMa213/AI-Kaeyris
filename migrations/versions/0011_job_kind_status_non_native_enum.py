"""convert jdr_jobs kind/status to non-native (VARCHAR) enums

BD fix: the native Postgres ENUM type ``jdr_job_kind`` was created with only
{TRANSCRIPTION, NARRATIVE, ELEMENTS, POVS} and never ALTER'd to add SUMMARY,
so persisting a summary job raised
``invalid input value for enum jdr_job_kind: "SUMMARY"`` on Postgres (SQLite
silently accepted it). This aligns ``jdr_jobs.kind``/``status`` with every
other enum column in the codebase: ``native_enum=False`` storing the lowercase
``.value`` (``summary``…) in a plain VARCHAR, dropping the native enum types.

Revision ID: e3f1a9b27c40
Revises: d9a7c3e5f1b2
Create Date: 2026-06-12 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "e3f1a9b27c40"
down_revision: Union[str, Sequence[str], None] = "d9a7c3e5f1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Move the columns off the native enum types (lowercasing the stored
        # member names → values), then drop the now-unused types.
        op.execute(
            "ALTER TABLE jdr_jobs "
            "ALTER COLUMN kind TYPE VARCHAR(32) USING lower(kind::text)"
        )
        op.execute(
            "ALTER TABLE jdr_jobs "
            "ALTER COLUMN status TYPE VARCHAR(16) USING lower(status::text)"
        )
        op.execute("DROP TYPE IF EXISTS jdr_job_kind")
        op.execute("DROP TYPE IF EXISTS jdr_job_status")
    else:
        # SQLite: the column is already VARCHAR (no native enum); just
        # normalise any previously stored uppercase member names to values.
        op.execute("UPDATE jdr_jobs SET kind = lower(kind), status = lower(status)")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE TYPE jdr_job_kind AS ENUM "
            "('TRANSCRIPTION', 'NARRATIVE', 'ELEMENTS', 'POVS', 'SUMMARY')"
        )
        op.execute(
            "ALTER TABLE jdr_jobs ALTER COLUMN kind TYPE jdr_job_kind "
            "USING upper(kind)::jdr_job_kind"
        )
        op.execute(
            "CREATE TYPE jdr_job_status AS ENUM "
            "('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')"
        )
        op.execute(
            "ALTER TABLE jdr_jobs ALTER COLUMN status TYPE jdr_job_status "
            "USING upper(status)::jdr_job_status"
        )
    else:
        op.execute("UPDATE jdr_jobs SET kind = upper(kind), status = upper(status)")
