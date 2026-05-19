"""US1 / feature 002 — Pipeline transcription en mode `non_diarised`.

Le job `_transcribe_session` doit forker selon `session.transcription_mode` :
- `non_diarised` : concatène les segments adapter, chunker via
  `text_chunker`, écrit dans `jdr_chunks`, **n'écrit pas** dans
  `jdr_transcriptions`.
- `diarised` : comportement Jalon 5 inchangé (test miroir de
  non-régression).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.adapters.transcription import (
    TranscriptionAdapter,
    TranscriptionResult,
    TranscriptionSegment,
)
from app.jobs.jdr import _transcribe_session
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    AudioSource,
    Chunk,
    Role,
    Session,
    SessionState,
    Transcription,
    TranscriptionMode,
)


@dataclass
class FlowCtx:
    plain_token: str
    gm_key_id: UUID
    session_id: UUID
    audio_file: Path
    sessionmaker: async_sessionmaker


async def _seed(
    db_engine: AsyncEngine,
    monkeypatch,
    tmp_path: Path,
    *,
    mode: TranscriptionMode,
) -> FlowCtx:
    monkeypatch.setattr(
        "app.core.config.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    monkeypatch.setattr(
        "app.jobs.jdr.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    monkeypatch.setattr(
        "app.jobs.jdr.settings.TRANSCRIPTION_CHUNK_DURATION_SECONDS", 0
    )
    # Force a tiny chunk size so a transcript of ~60 chars produces several chunks.
    monkeypatch.setattr(
        "app.jobs.jdr.settings.KAEYRIS_CHUNK_MAX_CHARS", 30
    )

    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)

    plain = f"gm-flow-{mode.value}"
    session_id = uuid4()
    gm_id: UUID

    async with sm() as setup:
        gm = ApiKey(
            name=f"gm-flow-{uuid4().hex[:8]}",
            hash=PasswordHasher().hash(plain),
            role=Role.GM,
            status=ApiKeyStatus.ACTIVE,
        )
        setup.add(gm)
        await setup.flush()
        gm_id = gm.id

        setup.add(
            Session(
                id=session_id,
                title=f"Flow {mode.value}",
                recorded_at=datetime.now(UTC),
                gm_key_id=gm_id,
                state=SessionState.AUDIO_UPLOADED,
                transcription_mode=mode,
            )
        )
        setup.add(
            AudioSource(
                session_id=session_id,
                path=f"audios/{session_id}.m4a",
                sha256="a" * 64,
                size_bytes=14,
                duration_seconds=10,
            )
        )
        await setup.commit()

    audio_dir = tmp_path / "audios"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_file = audio_dir / f"{session_id}.m4a"
    audio_file.write_bytes(b"fake-m4a-bytes")

    return FlowCtx(
        plain_token=plain,
        gm_key_id=gm_id,
        session_id=session_id,
        audio_file=audio_file,
        sessionmaker=sm,
    )


def _patch_adapter(monkeypatch, adapter: TranscriptionAdapter):
    monkeypatch.setattr(
        "app.jobs.jdr.get_transcription_adapter", lambda: adapter
    )


class _LongMockAdapter:
    """Returns three segments whose concatenated text exceeds 30 chars."""

    async def transcribe(
        self, *, audio_path: str, language_hint: str | None = None
    ) -> TranscriptionResult:
        return TranscriptionResult(
            segments=[
                TranscriptionSegment(
                    "speaker_1", 0.0, 1.5, "Bonjour à tous les aventuriers."
                ),
                TranscriptionSegment(
                    "speaker_2", 1.5, 3.0, "Que cherchez-vous dans cette forêt ?"
                ),
                TranscriptionSegment(
                    "speaker_1", 3.0, 5.0, "Un trésor caché par les anciens."
                ),
            ],
            language=language_hint or "fr",
            model_used="mock:whisper",
            provider="mock",
        )


# ---------------------------------------------------------------------------
# Non-diarised : writes to jdr_chunks, NOT to jdr_transcriptions
# ---------------------------------------------------------------------------


async def test_transcribe_non_diarised_writes_chunks(
    db_engine, monkeypatch, tmp_path
):
    ctx = await _seed(
        db_engine, monkeypatch, tmp_path, mode=TranscriptionMode.NON_DIARISED
    )
    _patch_adapter(monkeypatch, _LongMockAdapter())

    await _transcribe_session(ctx.session_id)

    async with ctx.sessionmaker() as db:
        chunks = (
            await db.execute(
                select(Chunk)
                .where(Chunk.session_id == ctx.session_id)
                .order_by(Chunk.ordre)
            )
        ).scalars().all()
        transcription_row = await db.scalar(
            select(Transcription).where(Transcription.session_id == ctx.session_id)
        )
        session_row = await db.scalar(
            select(Session).where(Session.id == ctx.session_id)
        )
        audio_row = await db.scalar(
            select(AudioSource).where(AudioSource.session_id == ctx.session_id)
        )

    # Au moins 2 chunks (texte concaténé > max_chars=30)
    assert len(chunks) >= 2
    # ordre 0, 1, 2, ... sans trou
    assert [c.ordre for c in chunks] == list(range(len(chunks)))
    # text non vide pour tous les chunks
    assert all(c.text.strip() for c in chunks)
    # Le contenu d'origine est préservé (ignorant les espaces) à travers la concat
    joined = " ".join(c.text for c in chunks)
    assert "Bonjour" in joined
    assert "trésor" in joined or "tresor" in joined  # tolérance accents
    # PAS de row dans jdr_transcriptions
    assert transcription_row is None
    # State transcribed, audio purgé
    assert session_row.state == SessionState.TRANSCRIBED
    assert audio_row.purged_at is not None
    # Fichier audio supprimé du disque
    assert not ctx.audio_file.exists()


async def test_transcribe_non_diarised_no_chunking_when_short(
    db_engine, monkeypatch, tmp_path
):
    ctx = await _seed(
        db_engine, monkeypatch, tmp_path, mode=TranscriptionMode.NON_DIARISED
    )
    # Augmente le seuil pour que tout tienne en un chunk
    monkeypatch.setattr(
        "app.jobs.jdr.settings.KAEYRIS_CHUNK_MAX_CHARS", 10000
    )
    _patch_adapter(monkeypatch, _LongMockAdapter())

    await _transcribe_session(ctx.session_id)

    async with ctx.sessionmaker() as db:
        chunks = (
            await db.execute(
                select(Chunk).where(Chunk.session_id == ctx.session_id)
            )
        ).scalars().all()
    assert len(chunks) == 1


# ---------------------------------------------------------------------------
# Diarised regression : Jalon 5 path inchangé
# ---------------------------------------------------------------------------


async def test_transcribe_diarised_still_writes_transcription_not_chunks(
    db_engine, monkeypatch, tmp_path
):
    """Non-régression FR-014 : mode diarised continue comme avant."""
    ctx = await _seed(
        db_engine, monkeypatch, tmp_path, mode=TranscriptionMode.DIARISED
    )
    _patch_adapter(monkeypatch, _LongMockAdapter())

    await _transcribe_session(ctx.session_id)

    async with ctx.sessionmaker() as db:
        chunks = (
            await db.execute(
                select(Chunk).where(Chunk.session_id == ctx.session_id)
            )
        ).scalars().all()
        transcription_row = await db.scalar(
            select(Transcription).where(Transcription.session_id == ctx.session_id)
        )

    # ZERO chunk en mode diarised
    assert chunks == []
    # Row Transcription écrite normalement
    assert transcription_row is not None
    assert len(transcription_row.segments_json) == 3
