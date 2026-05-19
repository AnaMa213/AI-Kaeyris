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
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select, update

from app.adapters.llm import (
    PermanentLLMError,
    TransientLLMError,
    get_llm_adapter,
)
from app.adapters.transcription import (
    PermanentTranscriptionError,
    TranscriptionAdapter,
    TranscriptionResult,
    TranscriptionSegment,
    TransientTranscriptionError,
    get_transcription_adapter,
)
from app.core.config import settings
from app.core.db import get_sessionmaker
from app.jobs import PermanentJobError, TransientJobError
from app.services.jdr.audio import AudioChunkingError, chunked_audio
from app.services.jdr.db.models import (
    AudioSource,
    Pj,
    Session,
    SessionState,
    Transcription,
    TranscriptionMode,
)
from app.services.jdr.db.repositories import (
    ArtifactRepository,
    ChunkRepository,
    MappingRepository,
    TranscriptionRepository,
)
from app.services.jdr.text_chunker import chunk_text
from app.services.jdr.prompts import (
    ELEMENTS_SYSTEM_PROMPT,
    NARRATIVE_SYSTEM_PROMPT,
    POV_SYSTEM_PROMPT,
    SUMMARY_MAP_SYSTEM_PROMPT,
    SUMMARY_REDUCE_SYSTEM_PROMPT,
)

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


def generate_elements_job(session_id: UUID) -> None:
    """Generate the structured-elements card (US2) for a transcribed session.

    Sync wrapper for RQ. See :func:`_generate_elements`.
    """
    asyncio.run(_generate_elements(session_id))


def generate_povs_job(session_id: UUID) -> None:
    """Generate one POV artefact per mapped PJ for a transcribed session.

    Sync wrapper for RQ. See :func:`_generate_povs`.
    """
    asyncio.run(_generate_povs(session_id))


def generate_summary_job(session_id: UUID) -> None:
    """Generate the global session summary via map-reduce (feature 002).

    Sync wrapper for RQ. See :func:`_generate_summary`.
    """
    asyncio.run(_generate_summary(session_id))


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
        transcription_mode = session_row.transcription_mode
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
        result = await _transcribe_with_optional_chunking(
            adapter=adapter,
            audio_path=full_path,
            session_id=session_id,
            language_hint=settings.TRANSCRIPTION_LANGUAGE_HINT or None,
            chunk_duration_seconds=settings.TRANSCRIPTION_CHUNK_DURATION_SECONDS,
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
    except AudioChunkingError as exc:
        await _mark_session_failed(sessionmaker, session_id)
        raise PermanentJobError(f"Audio chunking failed: {exc}") from exc

    # --- Step 3: persist + transition + purge audio (single commit) ---------
    # Forks on session.transcription_mode (feature 002-non-diarised-mode).

    if transcription_mode is TranscriptionMode.NON_DIARISED:
        # Concatène le texte de tous les segments dans l'ordre du provider,
        # puis chunke par caractères (frontières naturelles).
        full_text = " ".join(s.text.strip() for s in result.segments if s.text.strip())
        chunks = chunk_text(full_text, max_chars=settings.KAEYRIS_CHUNK_MAX_CHARS)
        async with sessionmaker() as db:
            await ChunkRepository(db).bulk_create_for_session(
                session_id, texts=chunks
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
    else:
        # Mode diarised — comportement Jalon 5 inchangé (FR-014 non-régression).
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
        campaign_context = session_row.campaign_context

    # Step 2: build the user prompt from segments
    user_prompt = _build_user_prompt_with_context(
        campaign_context, _format_segments_for_narrative(segments)
    )

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


async def _generate_elements(session_id: UUID) -> None:
    """Build the four-category elements card (US2).

    Mirrors :func:`_generate_narrative` — same pre-conditions (the session
    must be ``transcribed``), same error remapping, same UPSERT pattern.
    The only thing that changes is the prompt and that the LLM is asked
    for a JSON document which we parse and normalise into four lists.
    """
    sessionmaker = get_sessionmaker()

    async with sessionmaker() as db:
        session_row = await db.scalar(
            select(Session).where(Session.id == session_id)
        )
        if session_row is None:
            raise PermanentJobError(f"Session {session_id} not found.")
        if session_row.state != SessionState.TRANSCRIBED:
            raise PermanentJobError(
                f"Session {session_id} is not transcribed "
                f"(state={session_row.state.value}); cannot generate elements."
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
        campaign_context = session_row.campaign_context

    user_prompt = _build_user_prompt_with_context(
        campaign_context, _format_segments_for_narrative(segments)
    )

    adapter = get_llm_adapter()
    try:
        raw = await adapter.complete(
            system=ELEMENTS_SYSTEM_PROMPT,
            user=user_prompt,
            max_tokens=settings.LLM_MAX_TOKENS_DEFAULT,
        )
    except TransientLLMError as exc:
        raise TransientJobError(str(exc)) from exc
    except PermanentLLMError as exc:
        raise PermanentJobError(str(exc)) from exc

    elements = _parse_elements_response(raw)

    model_used = f"{settings.LLM_PROVIDER}:{settings.LLM_MODEL}"
    async with sessionmaker() as db:
        await ArtifactRepository(db).upsert(
            session_id,
            kind="elements",
            content_json=elements,
            model_used=model_used,
        )
        await db.commit()


async def _generate_povs(session_id: UUID) -> None:
    """Build one POV summary per mapped PJ for a transcribed session.

    Pre-conditions checked here (same idiom as the other generators):
    session exists, ``state == transcribed``, transcription row present,
    at least one mapping row. A missing mapping is technically already
    blocked by the route's 409 check, but we keep the worker honest in
    case it is invoked via a different code path (CLI, retry).

    UPSERT one ``Artifact(kind='pov:<pj_id>')`` per mapping entry.
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
                f"(state={session_row.state.value}); cannot generate POVs."
            )
        transcription = await db.scalar(
            select(Transcription).where(Transcription.session_id == session_id)
        )
        if transcription is None:
            raise PermanentJobError(
                f"Session {session_id} has no transcription row "
                "even though state is 'transcribed' — data inconsistency."
            )
        mappings = await MappingRepository(db).get_for_session(session_id)
        if not mappings:
            raise PermanentJobError(
                f"Session {session_id} has no speaker-PJ mapping; "
                "set one via PUT /mapping before generating POVs."
            )
        # Load PJ rows so we can put their names in the prompt.
        pj_ids = [m.pj_id for m in mappings]
        pj_rows = (
            await db.execute(select(Pj).where(Pj.id.in_(pj_ids)))
        ).scalars().all()
        pj_by_id: dict[UUID, Pj] = {p.id: p for p in pj_rows}

        segments = list(transcription.segments_json or [])
        campaign_context = session_row.campaign_context

    # Step 2: per-PJ LLM calls, then UPSERT in a single final commit
    adapter = get_llm_adapter()
    model_used = f"{settings.LLM_PROVIDER}:{settings.LLM_MODEL}"
    transcript_block = _format_segments_for_narrative(segments)

    results: list[tuple[UUID, str]] = []
    for mapping in mappings:
        pj = pj_by_id.get(mapping.pj_id)
        if pj is None:
            # Mapping points at a PJ that disappeared between the load and
            # now (very rare). Skip rather than fail the whole batch.
            logger.warning(
                "pov.skip_unknown_pj",
                extra={
                    "session_id": str(session_id),
                    "pj_id": str(mapping.pj_id),
                },
            )
            continue
        user_prompt = _build_pov_user_prompt(
            pj_name=pj.name,
            speaker_label=mapping.speaker_label,
            transcript_block=transcript_block,
            campaign_context=campaign_context,
        )
        try:
            text = await adapter.complete(
                system=POV_SYSTEM_PROMPT,
                user=user_prompt,
                max_tokens=settings.LLM_MAX_TOKENS_DEFAULT,
            )
        except TransientLLMError as exc:
            raise TransientJobError(str(exc)) from exc
        except PermanentLLMError as exc:
            raise PermanentJobError(str(exc)) from exc
        results.append((pj.id, text))

    async with sessionmaker() as db:
        repo = ArtifactRepository(db)
        for pj_id, text in results:
            await repo.upsert(
                session_id,
                kind=f"pov:{pj_id}",
                content_json={"text": text},
                model_used=model_used,
            )
        await db.commit()


def _build_pov_user_prompt(
    *,
    pj_name: str,
    speaker_label: str,
    transcript_block: str,
    campaign_context: str | None,
) -> str:
    """Wrap the transcript with a PJ-scoped header for the POV prompt."""
    header = (
        f"POINT DE VUE DE : {pj_name}\n"
        f"LABEL DE LOCUTEUR ASSOCIÉ : {speaker_label}\n"
        "Tous les autres labels (speaker_*, unknown) sont d'autres "
        "joueurs ou le MJ.\n"
    )
    base = f"{header}\n---\n\nTRANSCRIPTION DE LA SESSION :\n{transcript_block}"
    if not campaign_context or not campaign_context.strip():
        return base
    return (
        f"{header}\n"
        "CONTEXTE DE CAMPAGNE (informations hors-transcript, à utiliser "
        "uniquement pour ancrer noms récurrents, ton et fil narratif) :\n"
        f"{campaign_context.strip()}\n\n"
        "---\n\n"
        f"TRANSCRIPTION DE LA SESSION :\n{transcript_block}"
    )


# ---------------------------------------------------------------------------
# Feature 002 — global summary map-reduce
# ---------------------------------------------------------------------------


_SUMMARY_CHUNK_SEPARATOR = "\n\n---\n\n"


async def _generate_summary(session_id: UUID) -> None:
    """Map-reduce session summary for `non_diarised` sessions (FR-006/007).

    Step 0: load + validate (mode, state, ≥1 chunk).
    Step 1 (reset transaction): NULL every chunks.summary_text + DELETE
        artifacts(kind IN ('narrative', 'elements') OR kind LIKE 'pov:%').
        Committed before any LLM call so that DB locks don't span the
        long LLM phase (research.md §2). The old `summary` artifact
        survives this step — it's only overwritten at the end.
    Step 2 (map): one LLM call per chunk, in `ordre` ASC, persisted
        inline via ChunkRepository.update_summary_text. One commit per
        chunk to keep transactions short.
    Step 3 (reduce): if > 1 chunk, consolidate the partial summaries
        via one more LLM call. Otherwise the single partial summary is
        used as-is.
    Step 4: UPSERT artifacts(kind='summary'). Single commit.
    """
    sessionmaker = get_sessionmaker()

    # --- Step 0 : load + validate ------------------------------------------
    async with sessionmaker() as db:
        session_row = await db.scalar(
            select(Session).where(Session.id == session_id)
        )
        if session_row is None:
            raise PermanentJobError(f"Session {session_id} not found.")
        if session_row.transcription_mode is not TranscriptionMode.NON_DIARISED:
            raise PermanentJobError(
                f"Session {session_id} is not in non_diarised mode; "
                "summary job is reserved for non_diarised sessions."
            )
        if session_row.state != SessionState.TRANSCRIBED:
            raise PermanentJobError(
                f"Session {session_id} is not transcribed "
                f"(state={session_row.state.value}); cannot generate summary."
            )
        chunks = await ChunkRepository(db).list_for_session(session_id)
        if not chunks:
            raise PermanentJobError(
                f"Session {session_id} has no chunks; cannot generate summary."
            )
        # Capture light projection (id, ordre, text) so we don't hold ORM
        # objects across DB sessions.
        chunk_data = [(c.id, c.ordre, c.text) for c in chunks]

    # --- Step 1 : reset transaction (cascade FR-011) ------------------------
    from sqlalchemy import delete as sa_delete

    from app.services.jdr.db.models import Artifact as ArtifactModel

    async with sessionmaker() as db:
        await ChunkRepository(db).reset_summary_texts(session_id)
        await db.execute(
            sa_delete(ArtifactModel).where(
                ArtifactModel.session_id == session_id,
                ArtifactModel.kind.in_(("narrative", "elements")),
            )
        )
        await db.execute(
            sa_delete(ArtifactModel).where(
                ArtifactModel.session_id == session_id,
                ArtifactModel.kind.like("pov:%"),
            )
        )
        await db.commit()

    # --- Step 2 : map -------------------------------------------------------
    adapter = get_llm_adapter()
    partial_summaries: list[str] = []
    for chunk_id, _ordre, text in chunk_data:
        try:
            partial = await adapter.complete(
                system=SUMMARY_MAP_SYSTEM_PROMPT,
                user=text,
                max_tokens=settings.LLM_MAX_TOKENS_DEFAULT,
            )
        except TransientLLMError as exc:
            raise TransientJobError(str(exc)) from exc
        except PermanentLLMError as exc:
            raise PermanentJobError(str(exc)) from exc
        partial_summaries.append(partial)
        async with sessionmaker() as db:
            await ChunkRepository(db).update_summary_text(
                chunk_id, summary_text=partial
            )
            await db.commit()

    # --- Step 3 : reduce ----------------------------------------------------
    if len(partial_summaries) == 1:
        final_text = partial_summaries[0]
    else:
        reduce_user_prompt = _SUMMARY_CHUNK_SEPARATOR.join(partial_summaries)
        try:
            final_text = await adapter.complete(
                system=SUMMARY_REDUCE_SYSTEM_PROMPT,
                user=reduce_user_prompt,
                max_tokens=settings.LLM_MAX_TOKENS_DEFAULT,
            )
        except TransientLLMError as exc:
            raise TransientJobError(str(exc)) from exc
        except PermanentLLMError as exc:
            raise PermanentJobError(str(exc)) from exc

    # --- Step 4 : UPSERT summary artifact -----------------------------------
    model_used = f"{settings.LLM_PROVIDER}:{settings.LLM_MODEL}"
    async with sessionmaker() as db:
        await ArtifactRepository(db).upsert(
            session_id,
            kind="summary",
            content_json={"text": final_text},
            model_used=model_used,
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _transcribe_with_optional_chunking(
    *,
    adapter: TranscriptionAdapter,
    audio_path: Path,
    session_id: UUID,
    language_hint: str | None,
    chunk_duration_seconds: int,
) -> TranscriptionResult:
    """Run the transcription against the adapter, possibly chunk-by-chunk.

    When ``chunk_duration_seconds == 0`` (tests, or operator-disabled), this
    is a thin pass-through to ``adapter.transcribe(audio_path)``.

    Otherwise the audio is split into fixed-length WAV chunks via ffmpeg,
    each piece is transcribed in isolation, and the segments are stitched
    back together with their timestamps shifted by the chunk's start
    offset. Chunking is the only client-side defence against the Whisper
    repetition-loop failure mode on long sessions: a hallucination on one
    chunk cannot bleed into the next because each chunk is decoded fresh.

    The temp directory under ``data/.tmp/chunks/<session_id>/`` is removed
    on exit by ``chunked_audio`` regardless of success.
    """
    if chunk_duration_seconds <= 0:
        return await adapter.transcribe(
            audio_path=str(audio_path),
            language_hint=language_hint,
        )

    work_dir = (
        Path(settings.KAEYRIS_DATA_DIR) / ".tmp" / "chunks" / str(session_id)
    )
    all_segments: list[TranscriptionSegment] = []
    language = ""
    model_used = ""
    provider = ""
    with chunked_audio(audio_path, chunk_duration_seconds, work_dir) as chunks:
        for offset, chunk_path in chunks:
            chunk_result = await adapter.transcribe(
                audio_path=str(chunk_path),
                language_hint=language_hint,
            )
            for seg in chunk_result.segments:
                all_segments.append(
                    TranscriptionSegment(
                        speaker_label=seg.speaker_label,
                        start_seconds=seg.start_seconds + offset,
                        end_seconds=seg.end_seconds + offset,
                        text=seg.text,
                    )
                )
            # The metadata fields are the same for every chunk in practice;
            # keep the last non-empty value rather than synthesising.
            language = chunk_result.language or language
            model_used = chunk_result.model_used or model_used
            provider = chunk_result.provider or provider

    return TranscriptionResult(
        segments=all_segments,
        language=language,
        model_used=model_used,
        provider=provider,
    )


def _build_user_prompt_with_context(
    campaign_context: str | None, transcript_block: str
) -> str:
    """Prepend the MJ's campaign-bible context to the transcript, if any.

    When the session has a ``campaign_context`` set (Lot 4c), we wrap it
    in an explicit ``CONTEXTE DE CAMPAGNE`` block so the LLM knows it is
    background information, not part of the transcript. The transcript
    follows in its own labelled block. The system prompts already
    instruct the model to stay faithful to the transcript — the context
    is meant to anchor recurring PNJ, the campaign tone, and the current
    story arc, not to license inventions.
    """
    if not campaign_context or not campaign_context.strip():
        return transcript_block
    return (
        "CONTEXTE DE CAMPAGNE (informations hors-transcript, à utiliser "
        "uniquement pour ancrer noms récurrents, ton et fil narratif) :\n"
        f"{campaign_context.strip()}\n\n"
        "---\n\n"
        "TRANSCRIPTION DE LA SESSION :\n"
        f"{transcript_block}"
    )


_ELEMENT_KEYS = ("npcs", "locations", "items", "clues")

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _parse_elements_response(raw: str) -> dict[str, list[dict[str, str]]]:
    """Normalise the LLM's reply into ``{npcs, locations, items, clues}``.

    Models that don't perfectly follow "JSON only, no preamble" still need
    to produce a useful artefact — we try, in order:

    1. ``json.loads`` on the raw string.
    2. Strip a fenced code block (```json … ```) and retry.
    3. Look for the outermost ``{ … }`` substring and retry.

    If all three fail, we fall back to the empty four-list shape rather
    than raising — the spec's acceptance scenario US 2.3 explicitly says
    an empty list is preferable to an absent one (and to a 500).
    """
    parsed = _try_parse_json(raw)
    if parsed is None:
        fence_match = _FENCED_JSON_RE.search(raw)
        if fence_match is not None:
            parsed = _try_parse_json(fence_match.group(1))
    if parsed is None:
        brace_start = raw.find("{")
        brace_end = raw.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            parsed = _try_parse_json(raw[brace_start : brace_end + 1])
    if parsed is None:
        logger.warning(
            "elements.parse_failed",
            extra={"raw_excerpt": raw[:200]},
        )
        parsed = {}

    out: dict[str, list[dict[str, str]]] = {}
    for key in _ELEMENT_KEYS:
        entries = parsed.get(key) if isinstance(parsed, dict) else None
        out[key] = _normalise_element_list(entries)
    return out


def _try_parse_json(payload: str) -> Any:
    try:
        return json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None


def _normalise_element_list(entries: object) -> list[dict[str, str]]:
    """Coerce a raw element list into ``[{"name": str, "description": str}]``."""
    if not isinstance(entries, list):
        return []
    out: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        description = str(entry.get("description", "")).strip()
        out.append({"name": name, "description": description})
    return out


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
    "_generate_elements",
    "_generate_narrative",
    "_generate_povs",
    "_generate_summary",
    "_transcribe_session",
    "generate_elements_job",
    "generate_narrative_job",
    "generate_povs_job",
    "generate_summary_job",
    "transcribe_session_job",
]
