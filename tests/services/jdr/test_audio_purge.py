"""BD-8 — DELETE /sessions/{id}/audio.

Purges an uploaded audio file and resets the session so a fresh upload can
follow. It is idempotent for already-created sessions, clears data derived
from the old audio, and refuses only active transcriptions.
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
    Artifact,
    AudioSource,
    Chunk,
    Job,
    JobKind,
    JobStatus,
    Role,
    Session,
    SessionState,
    Transcription,
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
) -> tuple[str, UUID, Path, str]:
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
    session_row = Session(
        id=session_id,
        title="Purge test",
        recorded_at=datetime.now(UTC),
        gm_key_id=gm.id,
        state=state,
    )
    db.add(session_row)
    await db.flush()

    current_job_id = f"job-{session_id.hex[:24]}"
    status_by_state = {
        SessionState.AUDIO_UPLOADED: JobStatus.QUEUED,
        SessionState.TRANSCRIBING: JobStatus.RUNNING,
        SessionState.TRANSCRIPTION_FAILED: JobStatus.FAILED,
        SessionState.TRANSCRIBED: JobStatus.SUCCEEDED,
    }
    db.add(
        Job(
            id=current_job_id,
            kind=JobKind.TRANSCRIPTION,
            session_id=session_id,
            status=status_by_state.get(state, JobStatus.QUEUED),
            queued_at=datetime.now(UTC),
        )
    )
    await db.flush()
    session_row.current_job_id = current_job_id

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

    return plain_token, session_id, audio_file, current_job_id


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


async def test_purge_after_audio_uploaded_deletes_file_and_resets_state(
    tmp_path: Path, db_session: AsyncSession, make_db_session_dep, monkeypatch
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, session_id, audio_file, _current_job_id = await _seed_session_with_audio(
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
    await db_session.refresh(session_row)
    assert session_row.state == SessionState.CREATED
    assert session_row.current_job_id is None
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
    plain, session_id, audio_file, _current_job_id = await _seed_session_with_audio(
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
    assert session_row.current_job_id is None


async def test_purge_tolerates_missing_file_on_disk(
    tmp_path: Path, db_session: AsyncSession, make_db_session_dep, monkeypatch
):
    """The DB is the source of truth — a missing disk file is just a warning."""
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, session_id, audio_file, _current_job_id = await _seed_session_with_audio(
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
    assert session_row.current_job_id is None


# ---------------------------------------------------------------------------
# Protected and terminal paths
# ---------------------------------------------------------------------------


async def test_purge_returns_409_while_transcribing(
    tmp_path: Path, db_session: AsyncSession, make_db_session_dep, monkeypatch
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, session_id, audio_file, current_job_id = await _seed_session_with_audio(
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
    assert session_row.current_job_id == current_job_id


async def test_purge_after_transcribed_deletes_file_and_resets_state(
    tmp_path: Path, db_session: AsyncSession, make_db_session_dep, monkeypatch
):
    """Transcribed sessions are replaceable once transcription is idle."""
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, session_id, audio_file, _current_job_id = await _seed_session_with_audio(
        db_session,
        tmp_path / "audios",
        state=SessionState.TRANSCRIBED,
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
    assert session_row.current_job_id is None


async def test_purge_deletes_prepared_audio_and_raw_leftover(
    tmp_path: Path, db_session: AsyncSession, make_db_session_dep, monkeypatch
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, session_id, audio_file, _current_job_id = await _seed_session_with_audio(
        db_session,
        tmp_path / "audios",
        state=SessionState.TRANSCRIBED,
    )
    raw_leftover = tmp_path / ".tmp" / "audio-reduce" / str(session_id) / "raw.m4a"
    raw_leftover.parent.mkdir(parents=True, exist_ok=True)
    raw_leftover.write_bytes(b"raw-leftover")
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(
            f"/services/jdr/sessions/{session_id}/audio",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 204
    assert not audio_file.exists()
    assert not raw_leftover.exists()
    assert not raw_leftover.parent.exists()


async def test_purge_clears_transcription_chunks_artifacts_and_current_job(
    tmp_path: Path, db_session: AsyncSession, make_db_session_dep, monkeypatch
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, session_id, audio_file, current_job_id = await _seed_session_with_audio(
        db_session,
        tmp_path / "audios",
        state=SessionState.TRANSCRIBED,
    )
    raw_leftover = tmp_path / ".tmp" / "audio-reduce" / str(session_id) / "raw.m4a"
    raw_leftover.parent.mkdir(parents=True, exist_ok=True)
    raw_leftover.write_bytes(b"raw-leftover")
    session_before_purge = await db_session.scalar(
        select(Session).where(Session.id == session_id)
    )
    assert session_before_purge is not None
    session_before_purge.edited_transcript_md = "Old manual transcription"
    db_session.add(
        Transcription(
            session_id=session_id,
            segments_json=[{"speaker_label": "speaker_1", "text": "hello"}],
            language="fr",
            model_used="test:whisper",
            provider="test",
        )
    )
    db_session.add(
        Chunk(
            session_id=session_id,
            ordre=0,
            text="chunk text",
            summary_text="chunk summary",
        )
    )
    db_session.add(
        Artifact(
            session_id=session_id,
            kind="narrative",
            content_json={"text": "old narrative"},
            model_used="test:llm",
        )
    )
    db_session.add(
        Artifact(
            session_id=session_id,
            kind=f"pov:{uuid4()}",
            content_json={"text": "old pov"},
            model_used="test:llm",
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

    assert response.status_code == 204
    assert not audio_file.exists()
    assert not raw_leftover.exists()

    session_row = await db_session.scalar(
        select(Session).where(Session.id == session_id)
    )
    assert session_row is not None
    await db_session.refresh(session_row)
    assert session_row.state == SessionState.CREATED
    assert session_row.current_job_id is None
    assert session_row.edited_transcript_md is None

    audio_row = await db_session.scalar(
        select(AudioSource).where(AudioSource.session_id == session_id)
    )
    assert audio_row is not None
    assert audio_row.purged_at is not None

    assert (
        await db_session.scalar(
            select(Transcription).where(Transcription.session_id == session_id)
        )
    ) is None
    chunks = (
        await db_session.scalars(select(Chunk).where(Chunk.session_id == session_id))
    ).all()
    artifacts = (
        await db_session.scalars(
            select(Artifact).where(Artifact.session_id == session_id)
        )
    ).all()
    job_row = await db_session.scalar(select(Job).where(Job.id == current_job_id))
    assert chunks == []
    assert artifacts == []
    assert job_row is not None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


async def test_purge_created_without_audio_is_idempotent_204(
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

    assert response.status_code == 204
    session_row = await db_session.scalar(
        select(Session).where(Session.id == session_id)
    )
    assert session_row is not None
    assert session_row.state == SessionState.CREATED
    assert session_row.current_job_id is None


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
    plain_a, session_id, audio_file, _current_job_id = await _seed_session_with_audio(
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
