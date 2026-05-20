"""Regression test for the transcription_mode Enum DB lookup.

When ``jdr_sessions`` was extended in migration 0003 with
``transcription_mode VARCHAR(16) NOT NULL DEFAULT 'diarised'``,
existing rows inherit the *lowercase* value via the server default.

The ORM ``Enum(TranscriptionMode, ...)`` historically matched DB
strings to enum member *names* (uppercase: ``DIARISED``), making
every subsequent SELECT raise:

    LookupError: 'diarised' is not among the defined enum values.
    Enum name: jdr_transcription_mode.
    Possible values: DIARISED, NON_DIARISE..

The fix is ``values_callable=lambda enum_cls: [m.value for m in enum_cls]``
on the Enum type, which tells SQLAlchemy to match by ``.value``
(lowercase) instead of ``.name``. This test pins that behaviour.

Why we only fix ``transcription_mode`` and not the other Enum columns:
this is the only column whose migration injected the ``.value`` form via
``server_default``. The other Enum columns (mode/state/role/kind/status)
were always written through the ORM, which serialises by ``.name``
(uppercase) by default — so the DB rows already match the lookup.
Applying ``values_callable`` to those columns would invalidate
pre-existing UPPERCASE rows in production databases.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from sqlalchemy import select, text

from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    Role,
    Session,
    SessionState,
    TranscriptionMode,
)


@pytest.fixture
async def gm_key_id(db_session):
    gm = ApiKey(
        name=f"gm-{uuid4().hex[:8]}",
        hash=PasswordHasher().hash("noop"),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
    )
    db_session.add(gm)
    await db_session.commit()
    await db_session.refresh(gm)
    return gm.id


async def test_session_read_back_uses_server_default_diarised(
    db_session, gm_key_id
):
    """Reproduce the exact production path: a row whose transcription_mode
    column is populated by the SQL ``server_default 'diarised'`` (lowercase).

    Strategy: raw INSERT that omits ``transcription_mode`` entirely so the
    DB-side default kicks in — exactly like rows pre-existing migration 0003.
    The other Enum columns (mode/state) are inserted as their ``.name``
    UPPERCASE because that is what the ORM has always written.
    """
    session_id = uuid4()
    await db_session.execute(
        text(
            "INSERT INTO jdr_sessions "
            "(id, title, recorded_at, gm_key_id, mode, state, "
            "created_at, updated_at) "
            "VALUES (:id, :title, :recorded_at, :gm_key_id, 'BATCH', "
            "'CREATED', :now, :now)"
        ),
        {
            "id": session_id.hex,
            "title": "Pre-existing Jalon 5 session",
            "recorded_at": datetime.now(UTC),
            "gm_key_id": gm_key_id.hex,
            "now": datetime.now(UTC),
        },
    )
    await db_session.commit()

    # Sanity: the DB actually stored the lowercase server_default value.
    raw = await db_session.scalar(
        text(
            "SELECT transcription_mode FROM jdr_sessions WHERE id = :id"
        ),
        {"id": session_id.hex},
    )
    assert raw == "diarised", (
        f"server_default expected to write 'diarised', got {raw!r}"
    )

    # ORM SELECT — must NOT raise LookupError on the lowercase value.
    row = await db_session.scalar(
        select(Session).where(Session.id == session_id)
    )
    assert row is not None
    assert row.transcription_mode is TranscriptionMode.DIARISED


async def test_session_read_back_with_explicit_non_diarised_value(
    db_session, gm_key_id
):
    """Same regression for an explicit lowercase ``non_diarised`` value."""
    session_id = uuid4()
    await db_session.execute(
        text(
            "INSERT INTO jdr_sessions "
            "(id, title, recorded_at, gm_key_id, mode, state, "
            "transcription_mode, created_at, updated_at) "
            "VALUES (:id, :title, :recorded_at, :gm_key_id, 'BATCH', "
            "'CREATED', 'non_diarised', :now, :now)"
        ),
        {
            "id": session_id.hex,
            "title": "Non-diarised pre-existing",
            "recorded_at": datetime.now(UTC),
            "gm_key_id": gm_key_id.hex,
            "now": datetime.now(UTC),
        },
    )
    await db_session.commit()

    row = await db_session.scalar(
        select(Session).where(Session.id == session_id)
    )
    assert row is not None
    assert row.transcription_mode is TranscriptionMode.NON_DIARISED


async def test_session_read_back_after_normalising_uppercase_legacy_row(
    db_session, gm_key_id
):
    """Pin the migration 0004 path: a row inserted in DB as ``'NON_DIARISED'``
    (UPPERCASE, the format SQLAlchemy used to write before ``values_callable``
    was added) becomes ORM-readable after the normalisation UPDATE.

    Without the UPDATE, ``values_callable`` would now reject the uppercase
    literal — exactly the user-reported regression of the hotfix. Migration
    0004 (``UPDATE ... SET transcription_mode = LOWER(...)``) is what makes
    the legacy rows match the new lookup convention.
    """
    session_id = uuid4()
    # Simulate the pre-hotfix ORM write: literal UPPERCASE `.name`.
    await db_session.execute(
        text(
            "INSERT INTO jdr_sessions "
            "(id, title, recorded_at, gm_key_id, mode, state, "
            "transcription_mode, created_at, updated_at) "
            "VALUES (:id, :title, :recorded_at, :gm_key_id, 'BATCH', "
            "'CREATED', 'NON_DIARISED', :now, :now)"
        ),
        {
            "id": session_id.hex,
            "title": "Legacy uppercase row",
            "recorded_at": datetime.now(UTC),
            "gm_key_id": gm_key_id.hex,
            "now": datetime.now(UTC),
        },
    )
    await db_session.commit()

    # Apply the same SQL the Alembic migration 0004 runs.
    await db_session.execute(
        text(
            "UPDATE jdr_sessions "
            "SET transcription_mode = 'non_diarised' "
            "WHERE transcription_mode = 'NON_DIARISED'"
        )
    )
    await db_session.commit()

    row = await db_session.scalar(
        select(Session).where(Session.id == session_id)
    )
    assert row is not None
    assert row.transcription_mode is TranscriptionMode.NON_DIARISED


async def test_session_orm_insert_then_read_roundtrip(db_session, gm_key_id):
    """Sanity: inserting via the ORM (passing the enum member) and reading
    back returns the same member. With ``values_callable``, the ORM now
    serialises to ``.value`` (lowercase) too — consistent with what the
    migration's ``server_default`` writes."""
    row = Session(
        title="ORM roundtrip",
        recorded_at=datetime.now(UTC),
        gm_key_id=gm_key_id,
        state=SessionState.CREATED,
        transcription_mode=TranscriptionMode.NON_DIARISED,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)

    reread = await db_session.scalar(
        select(Session).where(Session.id == row.id)
    )
    assert reread is not None
    assert reread.transcription_mode is TranscriptionMode.NON_DIARISED
