"""BD-9 - transcription job prepares raw audio before adapter calls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.adapters.transcription import (
    PermanentTranscriptionError,
    TranscriptionResult,
    TranscriptionSegment,
)
from app.jobs import PermanentJobError
from app.jobs.jdr import _transcribe_session
from app.services.jdr.audio import AudioReduceError, PreparedAudioResult
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    AudioSource,
    Job,
    JobKind,
    JobStatus,
    Role,
    Session,
    SessionState,
)


@dataclass(slots=True)
class ReduceContext:
    session_id: UUID
    raw_file: Path
    prepared_file: Path
    sessionmaker: async_sessionmaker


@pytest_asyncio.fixture
async def reduce_ctx(
    tmp_path: Path,
    db_engine: AsyncEngine,
    monkeypatch,
) -> ReduceContext:
    monkeypatch.setattr("app.jobs.jdr.settings.KAEYRIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "app.jobs.jdr.settings.TRANSCRIPTION_CHUNK_DURATION_SECONDS", 0
    )
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)

    session_id = uuid4()
    raw_rel = f".tmp/audio-reduce/{session_id}/raw.m4a"
    raw_file = tmp_path / raw_rel
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    raw_file.write_bytes(b"raw-audio")
    prepared_file = tmp_path / "audios" / f"{session_id}.m4a"

    async with sm() as db:
        gm = ApiKey(
            name=f"gm-reduce-{session_id.hex[:8]}",
            hash=PasswordHasher().hash("gm-reduce-token"),
            role=Role.GM,
            status=ApiKeyStatus.ACTIVE,
        )
        db.add(gm)
        await db.flush()
        session = Session(
            id=session_id,
            title="Reduce worker",
            recorded_at=datetime.now(UTC),
            gm_key_id=gm.id,
            state=SessionState.AUDIO_UPLOADED,
        )
        db.add(session)
        db.add(
            AudioSource(
                session_id=session_id,
                path=raw_rel,
                sha256="a" * 64,
                size_bytes=9,
                duration_seconds=None,
            )
        )
        current_job_id = f"job-{session_id.hex[:24]}"
        db.add(
            Job(
                id=current_job_id,
                kind=JobKind.TRANSCRIPTION,
                session_id=session_id,
                status=JobStatus.QUEUED,
                queued_at=datetime.now(UTC),
            )
        )
        session.current_job_id = current_job_id
        await db.commit()

    return ReduceContext(
        session_id=session_id,
        raw_file=raw_file,
        prepared_file=prepared_file,
        sessionmaker=sm,
    )


class _RecordingAdapter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def transcribe(self, *, audio_path: str, language_hint: str | None = None):
        self.calls.append(audio_path)
        return TranscriptionResult(
            segments=[TranscriptionSegment("speaker_1", 0.0, 1.0, "hello")],
            language=language_hint or "fr",
            model_used="fake:whisper",
            provider="fake",
        )


class _FailingTranscriptionAdapter:
    async def transcribe(self, *, audio_path: str, language_hint: str | None = None):
        _ = audio_path, language_hint
        raise PermanentTranscriptionError("provider rejected prepared audio")


async def test_transcribe_prepares_raw_audio_before_adapter_call(
    reduce_ctx: ReduceContext,
    monkeypatch,
):
    adapter = _RecordingAdapter()
    monkeypatch.setattr("app.jobs.jdr.get_transcription_adapter", lambda: adapter)

    def fake_prepare(source: Path, target: Path) -> PreparedAudioResult:
        assert source == reduce_ctx.raw_file
        assert target == reduce_ctx.prepared_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"prepared-audio")
        return PreparedAudioResult(
            path=target,
            sha256="b" * 64,
            size_bytes=len(b"prepared-audio"),
        )

    monkeypatch.setattr("app.jobs.jdr.prepare_audio_for_transcription", fake_prepare)

    await _transcribe_session(reduce_ctx.session_id)

    assert adapter.calls == [str(reduce_ctx.prepared_file)]
    assert not reduce_ctx.raw_file.exists()
    assert reduce_ctx.prepared_file.read_bytes() == b"prepared-audio"

    async with reduce_ctx.sessionmaker() as db:
        audio = await db.scalar(
            select(AudioSource).where(AudioSource.session_id == reduce_ctx.session_id)
        )
        session = await db.get(Session, reduce_ctx.session_id)
    assert audio is not None
    assert audio.path == f"audios/{reduce_ctx.session_id}.m4a"
    assert audio.sha256 == "b" * 64
    assert audio.size_bytes == len(b"prepared-audio")
    assert session is not None
    assert session.state == SessionState.TRANSCRIBED


async def test_transcribe_does_not_expose_audio_reduce_job_or_state(
    reduce_ctx: ReduceContext,
    monkeypatch,
):
    adapter = _RecordingAdapter()
    monkeypatch.setattr("app.jobs.jdr.get_transcription_adapter", lambda: adapter)

    def fake_prepare(_source: Path, target: Path) -> PreparedAudioResult:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"prepared-audio")
        return PreparedAudioResult(
            path=target,
            sha256="c" * 64,
            size_bytes=len(b"prepared-audio"),
        )

    monkeypatch.setattr("app.jobs.jdr.prepare_audio_for_transcription", fake_prepare)

    await _transcribe_session(reduce_ctx.session_id)

    async with reduce_ctx.sessionmaker() as db:
        session = await db.get(Session, reduce_ctx.session_id)
        jobs = (
            await db.execute(select(Job).where(Job.session_id == reduce_ctx.session_id))
        ).scalars().all()

    assert session is not None
    assert session.state in {
        SessionState.AUDIO_UPLOADED,
        SessionState.TRANSCRIBING,
        SessionState.TRANSCRIBED,
        SessionState.TRANSCRIPTION_FAILED,
    }
    assert len(jobs) == 1
    assert jobs[0].id == session.current_job_id
    assert jobs[0].kind == JobKind.TRANSCRIPTION


async def test_transcribe_failure_after_prepare_keeps_prepared_audio(
    reduce_ctx: ReduceContext,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.jobs.jdr.get_transcription_adapter",
        lambda: _FailingTranscriptionAdapter(),
    )

    def fake_prepare(_source: Path, target: Path) -> PreparedAudioResult:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"prepared-before-provider-failure")
        return PreparedAudioResult(
            path=target,
            sha256="d" * 64,
            size_bytes=len(b"prepared-before-provider-failure"),
        )

    monkeypatch.setattr("app.jobs.jdr.prepare_audio_for_transcription", fake_prepare)

    with pytest.raises(PermanentJobError, match="provider rejected prepared audio"):
        await _transcribe_session(reduce_ctx.session_id)

    assert not reduce_ctx.raw_file.exists()
    assert reduce_ctx.prepared_file.read_bytes() == b"prepared-before-provider-failure"
    async with reduce_ctx.sessionmaker() as db:
        session = await db.get(Session, reduce_ctx.session_id)
        audio = await db.scalar(
            select(AudioSource).where(AudioSource.session_id == reduce_ctx.session_id)
        )
    assert session is not None
    assert session.state == SessionState.TRANSCRIPTION_FAILED
    assert audio is not None
    assert audio.path == f"audios/{reduce_ctx.session_id}.m4a"


async def test_transcribe_marks_session_failed_when_audio_reduce_fails(
    reduce_ctx: ReduceContext,
    monkeypatch,
):
    adapter = _RecordingAdapter()
    monkeypatch.setattr("app.jobs.jdr.get_transcription_adapter", lambda: adapter)
    monkeypatch.setattr(
        "app.jobs.jdr.prepare_audio_for_transcription",
        lambda _source, _target: (_ for _ in ()).throw(
            AudioReduceError("bad raw audio")
        ),
    )

    with pytest.raises(PermanentJobError, match="Audio reduce failed"):
        await _transcribe_session(reduce_ctx.session_id)

    assert adapter.calls == []
    assert not reduce_ctx.raw_file.exists()
    assert not reduce_ctx.prepared_file.exists()
    async with reduce_ctx.sessionmaker() as db:
        session = await db.get(Session, reduce_ctx.session_id)
        audio = await db.scalar(
            select(AudioSource).where(AudioSource.session_id == reduce_ctx.session_id)
        )
    assert session is not None
    assert session.state == SessionState.TRANSCRIPTION_FAILED
    assert audio is not None
    assert audio.path == f".tmp/audio-reduce/{reduce_ctx.session_id}/raw.m4a"
