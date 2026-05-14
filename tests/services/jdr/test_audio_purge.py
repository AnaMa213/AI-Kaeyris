"""Lot 4b — DELETE /sessions/{id}/audio.

Purges an uploaded audio file and resets the session so a fresh upload can
follow. Allowed states: ``audio_uploaded`` and ``transcription_failed``.
Refused (409) for ``transcribing`` and ``transcribed`` — see
``logic.purge_audio_for_session`` for the rationale.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import fakeredis
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.redis_client import get_redis
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    AudioSource,
    Role,
    Session,
    SessionState,
)
from app.services.jdr.router import router as jdr_router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_jdr_app(make_db_session_dep: Callable[..., object]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(jdr_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = lambda: fakeredis.FakeStrictRedis()
    return app


async def _seed_session_with_audio(
    db: AsyncSession,
    audio_dir: Path,
    *,
    state: SessionState,
    plain_token: str = "gm-purge-token",
    audio_already_purged: bool = False,
) -> tuple[str, UUID, Path]:
    """Insert GM + Session(state=state) + AudioSource + matching file on disk."""
    gm = ApiKey(
        name=f"gm-{uuid4().hex[:8]}",
        hash=PasswordHasher().hash(plain_token),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
    )
    db.add(gm)
    await db.flush()

    session_id = uuid4()
    db.add(
        Session(
            id=session_id,
            title="Purge test",
            recorded_at=datetime.now(UTC),
            gm_key_id=gm.id,
            state=state,
        )
    )
    audio_path_rel = f"audios/{session_id}.m4a"
    db.add(
        AudioSource(
            session_id=session_id,
            path=audio_path_rel,
            sha256="a" * 64,
            size_bytes=14,
            duration_seconds=10,
            purged_at=datetime.now(UTC) if audio_already_purged else None,
        )
    )
    await db.commit()

    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_file = audio_dir / f"{session_id}.m4a"
    audio_file.write_bytes(b"fake-m4a-bytes")

    return plain_token, session_id, audio_file


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


async def test_purge_after_audio_uploaded_deletes_file_and_resets_state(
    tmp_path: Path, db_session: AsyncSession, make_db_session_dep, monkeypatch
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, session_id, audio_file = await _seed_session_with_audio(
        db_session, tmp_path / "audios", state=SessionState.AUDIO_UPLOADED
    )
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(
            f"/services/jdr/sessions/{session_id}/audio",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 204
    assert response.content == b""
    assert not audio_file.exists()

    # State rolled back, audio row marked purged.
    session_row = await db_session.scalar(
        select(Session).where(Session.id == session_id)
    )
    assert session_row is not None
    assert session_row.state == SessionState.CREATED
    audio_row = await db_session.scalar(
        select(AudioSource).where(AudioSource.session_id == session_id)
    )
    assert audio_row is not None
    assert audio_row.purged_at is not None


async def test_purge_after_transcription_failed_works(
    tmp_path: Path, db_session: AsyncSession, make_db_session_dep, monkeypatch
):
    """The primary use case: a permanent failure left the audio orphan."""
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, session_id, audio_file = await _seed_session_with_audio(
        db_session, tmp_path / "audios", state=SessionState.TRANSCRIPTION_FAILED
    )
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(
            f"/services/jdr/sessions/{session_id}/audio",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 204
    assert not audio_file.exists()

    session_row = await db_session.scalar(
        select(Session).where(Session.id == session_id)
    )
    assert session_row is not None
    assert session_row.state == SessionState.CREATED


async def test_purge_tolerates_missing_file_on_disk(
    tmp_path: Path, db_session: AsyncSession, make_db_session_dep, monkeypatch
):
    """The DB is the source of truth — a missing disk file is just a warning."""
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, session_id, audio_file = await _seed_session_with_audio(
        db_session, tmp_path / "audios", state=SessionState.AUDIO_UPLOADED
    )
    # Delete the file before calling the endpoint — pretend the janitor
    # got there first, or the disk was wiped while the DB row survived.
    audio_file.unlink()
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(
            f"/services/jdr/sessions/{session_id}/audio",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 204
    session_row = await db_session.scalar(
        select(Session).where(Session.id == session_id)
    )
    assert session_row is not None
    assert session_row.state == SessionState.CREATED


# ---------------------------------------------------------------------------
# Refused states
# ---------------------------------------------------------------------------


async def test_purge_returns_409_while_transcribing(
    tmp_path: Path, db_session: AsyncSession, make_db_session_dep, monkeypatch
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, session_id, audio_file = await _seed_session_with_audio(
        db_session, tmp_path / "audios", state=SessionState.TRANSCRIBING
    )
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(
            f"/services/jdr/sessions/{session_id}/audio",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 409
    body = response.json()
    assert body["type"].endswith("/audio-purge-conflict")
    # File and state untouched.
    assert audio_file.exists()
    session_row = await db_session.scalar(
        select(Session).where(Session.id == session_id)
    )
    assert session_row is not None
    assert session_row.state == SessionState.TRANSCRIBING


async def test_purge_returns_409_when_already_transcribed(
    tmp_path: Path, db_session: AsyncSession, make_db_session_dep, monkeypatch
):
    """Transcribed sessions had their audio auto-purged — the call is a lie."""
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, session_id, _ = await _seed_session_with_audio(
        db_session,
        tmp_path / "audios",
        state=SessionState.TRANSCRIBED,
        audio_already_purged=True,
    )
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(
            f"/services/jdr/sessions/{session_id}/audio",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 409
    assert response.json()["type"].endswith("/audio-purge-conflict")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


async def test_purge_returns_404_when_no_audio_attached(
    tmp_path: Path, db_session: AsyncSession, make_db_session_dep, monkeypatch
):
    """Session exists but has never had an audio upload."""
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain = "gm-no-audio"
    gm = ApiKey(
        name="gm-no-audio",
        hash=PasswordHasher().hash(plain),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
    )
    db_session.add(gm)
    await db_session.flush()
    session_id = uuid4()
    db_session.add(
        Session(
            id=session_id,
            title="No audio yet",
            recorded_at=datetime.now(UTC),
            gm_key_id=gm.id,
            state=SessionState.CREATED,
        )
    )
    await db_session.commit()

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(
            f"/services/jdr/sessions/{session_id}/audio",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 404
    assert response.json()["type"].endswith("/audio-not-found")


async def test_purge_returns_404_for_unknown_session(
    db_session: AsyncSession, make_db_session_dep
):
    plain = "gm-purge-unknown"
    db_session.add(
        ApiKey(
            name="gm-purge-unknown",
            hash=PasswordHasher().hash(plain),
            role=Role.GM,
            status=ApiKeyStatus.ACTIVE,
        )
    )
    await db_session.commit()

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    unknown = "00000000-0000-0000-0000-000000000000"
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(
            f"/services/jdr/sessions/{unknown}/audio",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 404
    assert response.json()["type"].endswith("/session-not-found")


async def test_purge_cross_tenant_returns_404(
    tmp_path: Path, db_session: AsyncSession, make_db_session_dep, monkeypatch
):
    """Another GM cannot purge GM A's audio (FR-014 isolation)."""
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain_a, session_id, audio_file = await _seed_session_with_audio(
        db_session,
        tmp_path / "audios",
        state=SessionState.AUDIO_UPLOADED,
        plain_token="gm-a-token",
    )
    plain_b = "gm-b-token"
    db_session.add(
        ApiKey(
            name="gm-b",
            hash=PasswordHasher().hash(plain_b),
            role=Role.GM,
            status=ApiKeyStatus.ACTIVE,
        )
    )
    await db_session.commit()

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(
            f"/services/jdr/sessions/{session_id}/audio",
            headers={"Authorization": f"Bearer {plain_b}"},
        )

    assert response.status_code == 404
    assert response.json()["type"].endswith("/session-not-found")
    # GM A's audio is untouched.
    assert audio_file.exists()
    # Sanity: only used to make sure plain_a fixture variable is referenced.
    assert plain_a == "gm-a-token"
