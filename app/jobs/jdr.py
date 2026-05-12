"""RQ jobs for the JDR service.

ADR 0006. Each job is a plain synchronous function (RQ workers run sync
callables); the body wraps the real async logic with ``asyncio.run`` so
the implementation can use ``AsyncSession`` and ``TranscriptionAdapter``
naturally. Pickleable arguments only — primitives or UUIDs.

Tests call the async core (``_transcribe_session`` etc.) directly so
they can ``await`` it from a running event loop. RQ keeps the sync
entry point (``transcribe_session_job``).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, update

from app.adapters.transcription import (
    PermanentTranscriptionError,
    TranscriptionResult,
    TransientTranscriptionError,
    get_transcription_adapter,
)
from app.core.config import settings
from app.core.db import get_sessionmaker
from app.jobs import PermanentJobError, TransientJobError
from app.services.jdr.db.models import (
    AudioSource,
    Session,
    SessionState,
    Transcription,
)
from app.services.jdr.db.repositories import TranscriptionRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public sync entry points (registered with RQ)
# ---------------------------------------------------------------------------


def transcribe_session_job(session_id: UUID) -> None:
    """Transcribe the audio attached to a session.

    Sync wrapper around the async core so RQ can pickle the reference.
    See :func:`_transcribe_session` for the actual logic.
    """
    asyncio.run(_transcribe_session(session_id))


# ---------------------------------------------------------------------------
# Async cores (testable directly)
# ---------------------------------------------------------------------------


async def _transcribe_session(session_id: UUID) -> None:
    """Run the transcription pipeline for one session.

    Side effects:
    - moves the session state to ``transcribing`` then ``transcribed``
      (or ``transcription_failed`` on PermanentJobError)
    - persists the ``Transcription`` row (UPSERT semantics)
    - marks ``AudioSource.purged_at`` and deletes the audio file from disk
    """
    sessionmaker = get_sessionmaker()

    # --- Step 1: load session + audio, transition to TRANSCRIBING -----------

    async with sessionmaker() as db:
        session_row = await db.scalar(
            select(Session).where(Session.id == session_id)
        )
        if session_row is None:
            raise PermanentJobError(f"Session {session_id} not found.")

        audio = await db.scalar(
            select(AudioSource).where(AudioSource.session_id == session_id)
        )
        if audio is None:
            raise PermanentJobError(
                f"Session {session_id} has no audio source row."
            )
        if audio.purged_at is not None:
            raise PermanentJobError(
                f"Session {session_id} audio already purged — "
                "cannot re-transcribe without a fresh upload."
            )

        audio_path_relative = audio.path
        session_row.state = SessionState.TRANSCRIBING
        await db.commit()

    full_path = Path(settings.KAEYRIS_DATA_DIR) / audio_path_relative
    if not full_path.exists():
        await _mark_session_failed(sessionmaker, session_id)
        raise PermanentJobError(
            f"Audio file missing on disk: {full_path}"
        )

    # --- Step 2: call the adapter (long, no DB) -----------------------------

    adapter = get_transcription_adapter()
    try:
        result = await adapter.transcribe(
            audio_path=str(full_path),
            language_hint=settings.TRANSCRIPTION_LANGUAGE_HINT or None,
        )
    except TransientTranscriptionError as exc:
        # Roll back to AUDIO_UPLOADED so the retry picks it up.
        await _restore_session_state(
            sessionmaker, session_id, SessionState.AUDIO_UPLOADED
        )
        raise TransientJobError(str(exc)) from exc
    except PermanentTranscriptionError as exc:
        await _mark_session_failed(sessionmaker, session_id)
        raise PermanentJobError(str(exc)) from exc

    # --- Step 3: persist + transition + purge audio (single commit) ---------

    segments_json = _segments_to_json(result)
    async with sessionmaker() as db:
        await TranscriptionRepository(db).upsert(
            session_id,
            segments=segments_json,
            language=result.language,
            model_used=result.model_used,
            provider=result.provider,
        )
        await db.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(state=SessionState.TRANSCRIBED)
        )
        await db.execute(
            update(AudioSource)
            .where(AudioSource.session_id == session_id)
            .values(purged_at=datetime.now(UTC))
        )
        await db.commit()

    # --- Step 4: best-effort file deletion (DB is the source of truth) ------

    try:
        full_path.unlink()
    except OSError as exc:
        # DB already says purged; a stale file on disk is a janitor task.
        logger.warning(
            "Failed to delete audio file %s after successful transcription: %s",
            full_path,
            exc,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _mark_session_failed(sessionmaker, session_id: UUID) -> None:
    async with sessionmaker() as db:
        await db.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(state=SessionState.TRANSCRIPTION_FAILED)
        )
        await db.commit()


async def _restore_session_state(
    sessionmaker, session_id: UUID, state: SessionState
) -> None:
    async with sessionmaker() as db:
        await db.execute(
            update(Session).where(Session.id == session_id).values(state=state)
        )
        await db.commit()


def _segments_to_json(result: TranscriptionResult) -> list[dict]:
    return [
        {
            "speaker_label": s.speaker_label,
            "start_seconds": s.start_seconds,
            "end_seconds": s.end_seconds,
            "text": s.text,
        }
        for s in result.segments
    ]


# Re-exported so callers don't need to know about the ORM detail.
__all__ = [
    "Transcription",
    "_transcribe_session",
    "transcribe_session_job",
]
