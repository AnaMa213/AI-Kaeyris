"""US1 — Transcription job + GET /sessions/{id}/transcription route.

The job consumes an uploaded audio, calls TranscriptionAdapter, persists
the segments, purges the audio file from disk, and moves the session to
``state=transcribed``. The GET route exposes the JSON transcription.

These tests open their own short-lived sessions (rather than reusing
``db_session`` from conftest) because StaticPool + in-memory SQLite
serves the same connection across sessions: keeping multiple
async sessions open in parallel would race on the same connection.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import fakeredis
import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.adapters.transcription import (
    PermanentTranscriptionError,
    TranscriptionAdapter,
    TranscriptionResult,
    TranscriptionSegment,
    TransientTranscriptionError,
)
from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.redis_client import get_redis
from app.jobs import PermanentJobError, TransientJobError
from app.jobs.jdr import _transcribe_session
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    AudioSource,
    Role,
    Session,
    SessionState,
    Transcription,
)
from app.services.jdr.router import router as jdr_router


# ---------------------------------------------------------------------------
# Fixture: self-contained seed (no overlap with conftest's db_session)
# ---------------------------------------------------------------------------


@dataclass
class TranscriptionTestContext:
    """Holds everything a transcription test needs."""

    plain_token: str
    gm_key_id: UUID
    session_id: UUID
    audio_file: Path
    sessionmaker: async_sessionmaker


@pytest_asyncio.fixture
async def ctx(
    tmp_path: Path,
    db_engine: AsyncEngine,
    monkeypatch,
) -> TranscriptionTestContext:
    """Seed a GM + session + audio source, then close the setup session.

    The job's own session uses the same ``async_sessionmaker`` (patched
    via monkeypatch); StaticPool serves the same in-memory connection,
    but only one session is active at a time so no contention.
    """
    monkeypatch.setattr(
        "app.core.config.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    monkeypatch.setattr(
        "app.jobs.jdr.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )

    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)

    plain = "gm-transcribe-token"
    session_id = uuid4()
    gm_id: UUID

    async with sm() as setup_session:
        gm = ApiKey(
            name="gm-transcribe-test",
            hash=PasswordHasher().hash(plain),
            role=Role.GM,
            status=ApiKeyStatus.ACTIVE,
        )
        setup_session.add(gm)
        await setup_session.flush()
        gm_id = gm.id

        setup_session.add(
            Session(
                id=session_id,
                title="Transcription test",
                recorded_at=datetime.now(UTC),
                gm_key_id=gm_id,
                state=SessionState.AUDIO_UPLOADED,
            )
        )
        setup_session.add(
            AudioSource(
                session_id=session_id,
                path=f"audios/{session_id}.m4a",
                sha256="a" * 64,
                size_bytes=14,
                duration_seconds=10,
            )
        )
        await setup_session.commit()

    audio_dir = tmp_path / "audios"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_file = audio_dir / f"{session_id}.m4a"
    audio_file.write_bytes(b"fake-m4a-bytes")

    return TranscriptionTestContext(
        plain_token=plain,
        gm_key_id=gm_id,
        session_id=session_id,
        audio_file=audio_file,
        sessionmaker=sm,
    )


def _patch_transcription_adapter(monkeypatch, adapter: TranscriptionAdapter):
    monkeypatch.setattr(
        "app.jobs.jdr.get_transcription_adapter", lambda: adapter
    )


class _RaisingAdapter:
    """Test double for adapter error scenarios."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def transcribe(
        self, *, audio_path: str, language_hint: str | None = None
    ) -> TranscriptionResult:
        raise self._exc


class _DeterministicAdapter:
    """Returns fixed segments — independent of input."""

    async def transcribe(
        self, *, audio_path: str, language_hint: str | None = None
    ) -> TranscriptionResult:
        return TranscriptionResult(
            segments=[
                TranscriptionSegment("speaker_1", 0.0, 1.5, "Hello"),
                TranscriptionSegment("speaker_2", 1.5, 3.0, "Hi there"),
            ],
            language=language_hint or "fr",
            model_used="test:whisper",
            provider="test",
        )


# ---------------------------------------------------------------------------
# Job: happy path
# ---------------------------------------------------------------------------


async def test_transcribe_job_happy_path(ctx, monkeypatch):
    _patch_transcription_adapter(monkeypatch, _DeterministicAdapter())

    await _transcribe_session(ctx.session_id)

    async with ctx.sessionmaker() as db:
        transcription = await db.scalar(
            select(Transcription).where(
                Transcription.session_id == ctx.session_id
            )
        )
        assert transcription is not None
        assert transcription.language == "fr"
        assert transcription.provider == "test"
        assert len(transcription.segments_json) == 2
        assert transcription.segments_json[0]["speaker_label"] == "speaker_1"

        audio = await db.scalar(
            select(AudioSource).where(
                AudioSource.session_id == ctx.session_id
            )
        )
        assert audio is not None
        assert audio.purged_at is not None

        session_row = await db.scalar(
            select(Session).where(Session.id == ctx.session_id)
        )
        assert session_row is not None
        assert session_row.state == SessionState.TRANSCRIBED

    assert not ctx.audio_file.exists()


# ---------------------------------------------------------------------------
# Job: error mapping
# ---------------------------------------------------------------------------


async def test_transcribe_job_remaps_transient_error(ctx, monkeypatch):
    _patch_transcription_adapter(
        monkeypatch, _RaisingAdapter(TransientTranscriptionError("upstream 503"))
    )

    with pytest.raises(TransientJobError, match="upstream 503"):
        await _transcribe_session(ctx.session_id)


async def test_transcribe_job_remaps_permanent_error(ctx, monkeypatch):
    _patch_transcription_adapter(
        monkeypatch, _RaisingAdapter(PermanentTranscriptionError("bad audio"))
    )

    with pytest.raises(PermanentJobError, match="bad audio"):
        await _transcribe_session(ctx.session_id)

    # Session is marked failed so the MJ sees it on a subsequent poll.
    async with ctx.sessionmaker() as db:
        row = await db.scalar(
            select(Session).where(Session.id == ctx.session_id)
        )
        assert row is not None
        assert row.state == SessionState.TRANSCRIPTION_FAILED


# ---------------------------------------------------------------------------
# Job: edge cases
# ---------------------------------------------------------------------------


async def test_transcribe_job_unknown_session_raises_permanent(
    db_engine, monkeypatch
):
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)
    _patch_transcription_adapter(monkeypatch, _DeterministicAdapter())

    with pytest.raises(PermanentJobError, match="not found"):
        await _transcribe_session(uuid4())


async def test_transcribe_job_missing_audio_file_raises_permanent(
    ctx, monkeypatch
):
    ctx.audio_file.unlink()
    _patch_transcription_adapter(monkeypatch, _DeterministicAdapter())

    with pytest.raises(PermanentJobError, match=r"(?i)audio file"):
        await _transcribe_session(ctx.session_id)


async def test_transcribe_job_skips_already_purged_audio(ctx, monkeypatch):
    async with ctx.sessionmaker() as db:
        audio = await db.scalar(
            select(AudioSource).where(
                AudioSource.session_id == ctx.session_id
            )
        )
        assert audio is not None
        audio.purged_at = datetime.now(UTC)
        await db.commit()

    _patch_transcription_adapter(monkeypatch, _DeterministicAdapter())

    with pytest.raises(PermanentJobError, match="already purged"):
        await _transcribe_session(ctx.session_id)


async def test_transcribe_job_is_idempotent_on_rerun(ctx, monkeypatch):
    """Second run after success bails on the purged-audio check."""
    _patch_transcription_adapter(monkeypatch, _DeterministicAdapter())

    await _transcribe_session(ctx.session_id)
    assert not ctx.audio_file.exists()

    with pytest.raises(PermanentJobError, match="already purged"):
        await _transcribe_session(ctx.session_id)


# ---------------------------------------------------------------------------
# GET /sessions/{id}/transcription route
# ---------------------------------------------------------------------------


def _make_jdr_app(make_db_session_dep: Callable[..., Any]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(jdr_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = lambda: fakeredis.FakeStrictRedis()
    return app


async def test_get_transcription_returns_segments(
    ctx, make_db_session_dep, monkeypatch
):
    _patch_transcription_adapter(monkeypatch, _DeterministicAdapter())
    await _transcribe_session(ctx.session_id)

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{ctx.session_id}/transcription",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == str(ctx.session_id)
    assert body["language"] == "fr"
    assert body["provider"] == "test"
    assert len(body["segments"]) == 2
    assert body["segments"][0]["speaker_label"] == "speaker_1"
    assert body["segments"][0]["text"] == "Hello"


async def test_get_transcription_404_when_not_ready(ctx, make_db_session_dep):
    """Audio uploaded but transcription job not yet run -> 404."""
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{ctx.session_id}/transcription",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert response.status_code == 404
    body = response.json()
    assert body["type"].endswith("/transcription-not-ready")


async def test_get_transcription_404_for_unknown_session(
    ctx, make_db_session_dep
):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    unknown = "00000000-0000-0000-0000-000000000000"

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{unknown}/transcription",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert response.status_code == 404
    body = response.json()
    assert body["type"].endswith("/session-not-found")


async def test_get_transcription_cross_tenant_returns_404(
    ctx, make_db_session_dep, monkeypatch
):
    """Another GM cannot read a transcription that belongs to GM A."""
    _patch_transcription_adapter(monkeypatch, _DeterministicAdapter())
    await _transcribe_session(ctx.session_id)

    plain_b = "other-gm-token-do-not-use"
    async with ctx.sessionmaker() as db:
        db.add(
            ApiKey(
                name="other-gm",
                hash=PasswordHasher().hash(plain_b),
                role=Role.GM,
                status=ApiKeyStatus.ACTIVE,
            )
        )
        await db.commit()

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{ctx.session_id}/transcription",
            headers={"Authorization": f"Bearer {plain_b}"},
        )

    assert response.status_code == 404
    body = response.json()
    # Leaks less than "this resource exists but is not yours" — the 404 is
    # the session-not-found one, not the transcription-not-ready one.
    assert body["type"].endswith("/session-not-found")


# Re-exported for static checkers: see the import block at the top.
_ = UUID
