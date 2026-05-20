"""Normalize jdr_sessions.transcription_mode rows to lowercase

Revision ID: c7e3a2b04f56
Revises: a4d7e6b91f23
Create Date: 2026-05-20 14:30:00.000000

Hotfix follow-up to commit ``0cdca84`` (``fix(jdr): values_callable on
transcription_mode Enum to read server_default``).

Context
-------
Before that hotfix, SQLAlchemy serialised ``TranscriptionMode`` enum
values via the member ``.name`` (UPPERCASE). Every session created via
the ORM with an explicit ``transcription_mode=NON_DIARISED`` therefore
landed in DB as the literal string ``'NON_DIARISED'`` — while the
``server_default`` of migration 0003 wrote the ``.value`` form
(``'diarised'`` lowercase).

After the hotfix added ``values_callable``, SQLAlchemy now reads AND
writes by ``.value`` (lowercase). The UPPERCASE rows that were
historically inserted by the ORM became unreadable:

    LookupError: 'NON_DIARISED' is not among the defined enum values.
    Enum name: jdr_transcription_mode.
    Possible values: diarised, non_diarise..

This migration normalises every existing row to the lowercase form so
the DB matches the convention the application now uses. The conversion
is restricted to the two known UPPERCASE literals — we do not touch
rows that are already lowercase.

Downgrade reverses the conversion (UPPERCASE), although this is mostly
academic since the application code (post-hotfix) expects lowercase.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "c7e3a2b04f56"
down_revision: Union[str, Sequence[str], None] = "a4d7e6b91f23"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE jdr_sessions "
        "SET transcription_mode = 'diarised' "
        "WHERE transcription_mode = 'DIARISED'"
    )
    op.execute(
        "UPDATE jdr_sessions "
        "SET transcription_mode = 'non_diarised' "
        "WHERE transcription_mode = 'NON_DIARISED'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE jdr_sessions "
        "SET transcription_mode = 'DIARISED' "
        "WHERE transcription_mode = 'diarised'"
    )
    op.execute(
        "UPDATE jdr_sessions "
        "SET transcription_mode = 'NON_DIARISED' "
        "WHERE transcription_mode = 'non_diarised'"
    )
