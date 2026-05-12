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

from app.adapters.llm import (
    PermanentLLMError,
    TransientLLMError,
    get_llm_adapter,
)
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
from app.services.jdr.db.repositories import (
    ArtifactRepository,
    TranscriptionRepository,
)
from app.services.jdr.prompts import NARRATIVE_SYSTEM_PROMPT

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


def generate_narrative_job(session_id: UUID) -> None:
    """Generate a French narrative summary from the session's transcription.

    Sync wrapper for RQ. See :func:`_generate_narrative`.
    """
    asyncio.run(_generate_narrative(session_id))


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


async def _generate_narrative(session_id: UUID) -> None:
    """Build a French narrative summary of a transcribed session.

    Refuses to run if the session is not yet ``transcribed`` (PermanentJobError).
    Maps adapter errors to job errors so the RQ retry policy still applies.
    """
    sessionmaker = get_sessionmaker()

    # Step 1: load + validate
    async with sessionmaker() as db:
        session_row = await db.scalar(
            select(Session).where(Session.id == session_id)
        )
        if session_row is None:
            raise PermanentJobError(f"Session {session_id} not found.")
        if session_row.state != SessionState.TRANSCRIBED:
            raise PermanentJobError(
                f"Session {session_id} is not transcribed "
                f"(state={session_row.state.value}); cannot generate narrative."
            )
        transcription = await db.scalar(
            select(Transcription).where(Transcription.session_id == session_id)
        )
        if transcription is None:
            raise PermanentJobError(
                f"Session {session_id} has no transcription row "
                "even though state is 'transcribed' — data inconsistency."
            )
        segments = list(transcription.segments_json or [])

    # Step 2: build the user prompt from segments
    user_prompt = _format_segments_for_narrative(segments)

    # Step 3: call the LLM
    adapter = get_llm_adapter()
    try:
        text = await adapter.complete(
            system=NARRATIVE_SYSTEM_PROMPT,
            user=user_prompt,
            max_tokens=settings.LLM_MAX_TOKENS_DEFAULT,
        )
    except TransientLLMError as exc:
        raise TransientJobError(str(exc)) from exc
    except PermanentLLMError as exc:
        raise PermanentJobError(str(exc)) from exc

    # Step 4: UPSERT the artifact
    model_used = f"{settings.LLM_PROVIDER}:{settings.LLM_MODEL}"
    async with sessionmaker() as db:
        await ArtifactRepository(db).upsert(
            session_id,
            kind="narrative",
            content_json={"text": text},
            model_used=model_used,
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_segments_for_narrative(segments: list[dict]) -> str:
    """Flatten the diarised segments into a chronological transcript.

    Each line: ``[t1.0s → t2.5s] speaker_label : texte``. The LLM uses
    this to write the narrative summary.

    TODO(tech-debt, jalon 5+): single-shot summarisation. For sessions
    longer than ~1h, the user prompt can reach 30-45k tokens which (a)
    risks "lost in the middle" with most models, (b) may exceed local
    Whisper hosts running on consumer GPUs (Llama 3.1 8B with a 32k
    context fits ~16 GB VRAM on a RTX 4090 — beyond that, the prefill
    blows up). The plan is to introduce a map-reduce summarisation
    strategy (chunk into ~5-10 min pieces, summarise each, then combine)
    when the first real session shows quality issues. See conversation
    on 2026-05-13 for the rationale; the chunking will be a dedicated
    sub-lot before US3 (POV summaries, where per-PJ context inflates
    the token count even more).
    """
    lines: list[str] = []
    for seg in segments:
        start = float(seg.get("start_seconds", 0.0) or 0.0)
        end = float(seg.get("end_seconds", 0.0) or 0.0)
        label = str(seg.get("speaker_label", "unknown"))
        text = str(seg.get("text", "")).strip()
        lines.append(f"[{start:.1f}s → {end:.1f}s] {label} : {text}")
    return "\n".join(lines) if lines else "(transcription vide)"


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
    "_generate_narrative",
    "_transcribe_session",
    "generate_narrative_job",
    "transcribe_session_job",
]
