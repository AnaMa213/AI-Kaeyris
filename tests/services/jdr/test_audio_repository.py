"""BD-9 repository helpers for canonical session audio metadata."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    AudioSource,
    Role,
    Session,
)
from app.services.jdr.db.repositories import SessionRepository


async def test_update_audio_source_file_updates_canonical_metadata(
    db_session: AsyncSession,
):
    gm = ApiKey(
        name="gm-audio-repository",
        hash=PasswordHasher().hash("gm-audio-repository-token"),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
    )
    db_session.add(gm)
    await db_session.flush()

    session_id = uuid4()
    db_session.add(
        Session(
            id=session_id,
            title="Repository audio",
            recorded_at=datetime.now(UTC),
            gm_key_id=gm.id,
        )
    )
    db_session.add(
        AudioSource(
            session_id=session_id,
            path=f".tmp/audio-reduce/{session_id}/raw.m4a",
            sha256="a" * 64,
            size_bytes=123,
            duration_seconds=None,
        )
    )
    await db_session.commit()

    repo = SessionRepository(db_session)
    updated = await repo.update_audio_source_file(
        session_id,
        path=f"audios/{session_id}.m4a",
        sha256="b" * 64,
        size_bytes=42,
        duration_seconds=3600,
    )
    await db_session.commit()

    assert updated is not None
    assert updated.path == f"audios/{session_id}.m4a"
    assert updated.sha256 == "b" * 64
    assert updated.size_bytes == 42
    assert updated.duration_seconds == 3600
    assert updated.purged_at is None

    reread = await db_session.scalar(
        select(AudioSource).where(AudioSource.session_id == session_id)
    )
    assert reread is not None
    assert reread.path == f"audios/{session_id}.m4a"
