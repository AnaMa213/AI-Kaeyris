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
import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from rq import get_current_job
from sqlalchemy import select, update

from app.adapters.llm import (
    LLMAdapter,
    PermanentLLMError,
    TransientLLMError,
    build_llm_adapter,
    build_local_llm_adapter,
    build_personal_cloud_llm_adapter,
    get_llm_adapter,
)
from app.adapters.transcription import (
    PermanentTranscriptionError,
    TranscriptionAdapter,
    TranscriptionResult,
    TranscriptionSegment,
    TransientTranscriptionError,
    build_local_transcription_adapter,
    build_personal_cloud_transcription_adapter,
    get_transcription_adapter,
)
from app.core.config import settings
from app.core.db import get_sessionmaker
from app.core.logging import get_logger
from app.core.metrics import JOB_DURATION_SECONDS, JOBS_TOTAL
from app.core.models import User
from app.jobs import PermanentJobError, TransientJobError
from app.services.jdr.audio import (
    AudioChunkingError,
    AudioReduceError,
    chunked_audio,
    prepare_audio_for_transcription,
)
from app.services.jdr.elements import flatten_elements
from app.services.jdr.db.models import (
    AudioSource,
    Campaign,
    Job,
    JobStatus,
    ModelProvider,
    ModelSettings,
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
    ModelSettingsRepository,
    SessionPlayerRepository,
    SessionRepository,
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

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Per-user model settings routing (BD-19)
# ---------------------------------------------------------------------------


async def _resolve_session_owner_id(session_id: UUID) -> UUID | None:
    """Resolve the owning web user for a session, if one can be found."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as db:
        session_row = await db.scalar(
            select(Session).where(Session.id == session_id)
        )
        if session_row is None:
            return None

        if session_row.campaign_id is not None:
            campaign = await db.scalar(
                select(Campaign).where(Campaign.id == session_row.campaign_id)
            )
            if campaign is not None:
                return campaign.owner_user_id

        return await db.scalar(
            select(User.id).where(User.api_key_id == session_row.gm_key_id)
        )


async def _load_user_model_settings(user_id: UUID) -> ModelSettings | None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as db:
        return await ModelSettingsRepository(db).get_for_user(user_id)


async def _load_session_model_settings(session_id: UUID) -> ModelSettings | None:
    owner_id = await _resolve_session_owner_id(session_id)
    if owner_id is None:
        return None
    return await _load_user_model_settings(owner_id)


def _build_llm_adapter_for_user(row: ModelSettings | None) -> LLMAdapter:
    """Build an LLM adapter from user settings, preserving env fallback."""
    # Story 7.2 / BD-22: a NULL provider means "no per-user override" — inherit
    # the operator/env adapter, exactly as when no row exists.
    if row is None or row.summary_provider is None:
        return get_llm_adapter()

    if row.summary_provider is ModelProvider.OLLAMA:
        return build_llm_adapter(
            provider="ollama",
            model=row.ollama_model or settings.LLM_MODEL,
            api_key="noop",
        )

    if row.summary_provider is ModelProvider.CLOUD:
        if row.deepinfra_api_key:
            return build_personal_cloud_llm_adapter(
                model=row.summary_cloud_model or settings.LLM_MODEL,
                api_key=row.deepinfra_api_key,
            )
        return get_llm_adapter()

    if row.summary_provider is ModelProvider.LOCAL:
        if not row.summary_local_path or not row.summary_local_validation_hash:
            raise PermanentJobError(
                "Validated local summary settings are required before running Local jobs."
            )
        return build_local_llm_adapter(model_path=row.summary_local_path)

    return get_llm_adapter()


def _build_transcription_adapter_for_user(
    row: ModelSettings | None,
) -> TranscriptionAdapter:
    """Build a transcription adapter from user settings, preserving env fallback."""
    # Story 7.2 / BD-22: a NULL provider means "no per-user override" — inherit
    # the operator/env adapter, exactly as when no row exists.
    if row is None or row.transcription_provider is None:
        return get_transcription_adapter()

    if row.transcription_provider is ModelProvider.CLOUD:
        if row.deepinfra_api_key:
            return build_personal_cloud_transcription_adapter(
                model=row.transcription_cloud_model
                or settings.TRANSCRIPTION_MODEL,
                api_key=row.deepinfra_api_key,
            )
        return get_transcription_adapter()

    if row.transcription_provider is ModelProvider.LOCAL:
        if (
            not row.transcription_local_path
            or not row.transcription_local_validation_hash
        ):
            raise PermanentJobError(
                "Validated local transcription settings are required before "
                "running Local jobs."
            )
        return build_local_transcription_adapter(
            model_path=row.transcription_local_path
        )

    logger.warning(
        "transcription_adapter.ollama_not_supported",
        note="Ollama is supported for LLM jobs only; falling back to operator config.",
    )
    return get_transcription_adapter()


def _llm_model_used(adapter: LLMAdapter) -> str:
    provider = getattr(adapter, "provider", settings.LLM_PROVIDER)
    model = getattr(adapter, "model", settings.LLM_MODEL)
    return f"{provider}:{model}"


# ---------------------------------------------------------------------------
# Public sync entry points (registered with RQ)
# ---------------------------------------------------------------------------


class _ProgressReporter:
    """Best-effort writer of transcription progress onto the current RQ job.

    BD-10: progress is transient UX state stored on ``job.meta`` and read
    back by ``GET /jobs/{id}`` (RQ documents ``job.meta`` + ``save_meta()``:
    https://python-rq.org/docs/jobs/). A ``None`` job — code running outside
    a worker, e.g. the direct async tests — turns every call into a no-op,
    so the transcription core stays queue-agnostic and unit-testable.

    Emitting a ``phase`` without a ``progress_percent`` deliberately leaves
    the last stored percent untouched: a terminal ``failed`` must not erase
    the last known progress (BD-10 US3).
    """

    def __init__(self, job: Any) -> None:
        self._job = job

    def __call__(self, phase: str, progress_percent: int | None = None) -> None:
        if self._job is None:
            return
        self._job.meta["phase"] = phase
        if progress_percent is not None:
            self._job.meta["progress_percent"] = progress_percent
        try:
            self._job.save_meta()
        except Exception as exc:  # never let progress telemetry fail the job
            logger.warning("transcribe.progress_save_failed", error=str(exc))


def _run_job_with_metrics(kind: str, coro) -> None:
    """Run an async job core inside the sync RQ entry point + record metrics.

    Tracks duration in :data:`JOB_DURATION_SECONDS` and increments
    :data:`JOBS_TOTAL` with an ``outcome`` label in:
    - ``succeeded`` — the async core completed without raising
    - ``transient`` — raised :class:`TransientJobError` (RQ will retry)
    - ``permanent`` — raised :class:`PermanentJobError` (definitive fail)
    - ``failed`` — raised any other exception (programming error)
    """
    start = time.perf_counter()
    outcome = "succeeded"
    try:
        asyncio.run(coro)
    except TransientJobError:
        outcome = "transient"
        raise
    except PermanentJobError:
        outcome = "permanent"
        raise
    except Exception:
        outcome = "failed"
        raise
    finally:
        duration = time.perf_counter() - start
        JOB_DURATION_SECONDS.labels(kind=kind).observe(duration)
        JOBS_TOTAL.labels(kind=kind, outcome=outcome).inc()


def transcribe_session_job(session_id: UUID) -> None:
    """Transcribe the audio attached to a session.

    Sync wrapper around the async core so RQ can pickle the reference.
    Looks up the current RQ job here (the only spot with worker context)
    and hands the async core a queue-agnostic progress reporter (BD-10).
    See :func:`_transcribe_session` for the actual logic.
    """
    reporter = _ProgressReporter(get_current_job())
    _run_job_with_metrics(
        "transcription", _transcribe_session(session_id, report_progress=reporter)
    )


def generate_narrative_job(session_id: UUID) -> None:
    """Generate a French narrative summary from the session's transcription.

    Sync wrapper for RQ. See :func:`_generate_narrative`.
    """
    _run_job_with_metrics("narrative", _generate_narrative(session_id))


def generate_elements_job(session_id: UUID) -> None:
    """Generate the structured-elements card (US2) for a transcribed session.

    Sync wrapper for RQ. See :func:`_generate_elements`.
    """
    _run_job_with_metrics("elements", _generate_elements(session_id))


def generate_povs_job(session_id: UUID) -> None:
    """Generate one POV artefact per mapped PJ for a transcribed session.

    Sync wrapper for RQ. See :func:`_generate_povs`.
    """
    _run_job_with_metrics("povs", _generate_povs(session_id))


def generate_summary_job(session_id: UUID) -> None:
    """Generate the global session summary via map-reduce (feature 002).

    Sync wrapper for RQ. See :func:`_generate_summary`.
    """
    _run_job_with_metrics("summary", _generate_summary(session_id))


# ---------------------------------------------------------------------------
# Async cores (testable directly)
# ---------------------------------------------------------------------------


async def _transcribe_session(
    session_id: UUID,
    *,
    report_progress: Callable[[str, int | None], None] | None = None,
) -> None:
    """Run the transcription pipeline for one session.

    Side effects:
    - moves the session state to ``transcribing`` then ``transcribed``
      (or ``transcription_failed`` on PermanentJobError)
    - persists the ``Transcription`` row (UPSERT semantics)
    - keeps the source audio available until explicit destructive deletion

    ``report_progress`` (BD-10) is an optional ``(phase, progress_percent)``
    reporter. It is ``None`` for the direct async tests, which keeps this
    core queue-agnostic; the RQ entry point passes a :class:`_ProgressReporter`
    bound to the live job.
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
        duration_seconds = audio.duration_seconds
        transcription_mode = session_row.transcription_mode
        session_row.state = SessionState.TRANSCRIBING
        await db.commit()

    full_path = Path(settings.KAEYRIS_DATA_DIR) / audio_path_relative
    if not full_path.exists():
        await _mark_session_failed(sessionmaker, session_id)
        raise PermanentJobError(
            f"Audio file missing on disk: {full_path}"
        )

    if _is_transient_raw_audio_path(audio_path_relative):
        prepared_path = _prepared_audio_path_for_session(session_id)
        try:
            prepared = prepare_audio_for_transcription(full_path, prepared_path)
        except AudioReduceError as exc:
            _cleanup_transient_audio_dir(session_id)
            await _mark_session_failed(
                sessionmaker,
                session_id,
                failure_reason=f"Audio reduce failed: {exc}",
            )
            raise PermanentJobError(f"Audio reduce failed: {exc}") from exc

        async with sessionmaker() as db:
            updated_audio = await SessionRepository(db).update_audio_source_file(
                session_id,
                path=_relative_data_path(prepared.path),
                sha256=prepared.sha256,
                size_bytes=prepared.size_bytes,
                duration_seconds=duration_seconds,
            )
            if updated_audio is None:
                await db.rollback()
                await _mark_session_failed(
                    sessionmaker,
                    session_id,
                    failure_reason="Audio source row missing after preparation.",
                )
                raise PermanentJobError(
                    f"Session {session_id} has no audio source row after preparation."
                )
            await db.commit()

        _cleanup_transient_audio_dir(session_id)
        full_path = prepared.path

    # --- Step 2: call the adapter (long, no DB) -----------------------------

    user_settings = await _load_session_model_settings(session_id)
    adapter = _build_transcription_adapter_for_user(user_settings)
    chunk_duration = settings.TRANSCRIPTION_CHUNK_DURATION_SECONDS

    def _on_chunk(chunks_done: int, chunks_total: int) -> None:
        # Map the chunk-progress event onto a public percent. 100 is
        # reserved for terminal success, so the in-flight value is capped
        # at 99 (BD-10 research §5).
        if report_progress is None or chunks_total <= 0:
            return
        percent = min(99, round(chunks_done / chunks_total * 100))
        report_progress("transcribing", percent)

    if report_progress is not None:
        # Chunked runs re-encode/segment the audio first ("reducing");
        # single-shot jumps straight to transcribing. Either way start at 0.
        report_progress("reducing" if chunk_duration > 0 else "transcribing", 0)

    try:
        result = await _transcribe_with_optional_chunking(
            adapter=adapter,
            audio_path=full_path,
            session_id=session_id,
            language_hint=settings.TRANSCRIPTION_LANGUAGE_HINT or None,
            chunk_duration_seconds=chunk_duration,
            on_progress=_on_chunk,
        )
    except TransientTranscriptionError as exc:
        # Roll back to AUDIO_UPLOADED so the retry picks it up.
        await _restore_session_state(
            sessionmaker, session_id, SessionState.AUDIO_UPLOADED
        )
        if report_progress is not None:
            # No percent: keep the last known progress instead of resetting.
            report_progress("failed")
        raise TransientJobError(str(exc)) from exc
    except PermanentTranscriptionError as exc:
        await _mark_session_failed(sessionmaker, session_id)
        if report_progress is not None:
            report_progress("failed")
        raise PermanentJobError(str(exc)) from exc
    except AudioChunkingError as exc:
        await _mark_session_failed(sessionmaker, session_id)
        if report_progress is not None:
            report_progress("failed")
        raise PermanentJobError(f"Audio chunking failed: {exc}") from exc

    # --- Step 3: persist + transition (single commit) ----------------------
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
            await db.commit()

    # Persistence + session state transition succeeded — only now is the
    # transcription truly complete, so 100 is emitted here and nowhere else.
    if report_progress is not None:
        report_progress("done", 100)


def _edited_transcript_source(session_row: Session) -> str | None:
    content = session_row.edited_transcript_md
    if content is None:
        return None
    return content if content.strip() else None


async def _load_session_source_document(
    session_id: UUID,
    *,
    artefact_label: str,
) -> tuple[str, str | None]:
    """Return ``(source_text, campaign_context)`` for an artefact job.

    Forks on ``session.transcription_mode`` (feature 002):
    - **diarised**: returns the formatted segments (Jalon 5 behaviour).
    - **non_diarised**: returns the chunks' ``summary_text`` joined by
      ``_SUMMARY_CHUNK_SEPARATOR``. Raises ``PermanentJobError`` if any
      chunk's ``summary_text`` is NULL (FR-010 — caller must run summary
      job first; the route catches this state and returns 409 no-summary).

    Raises ``PermanentJobError`` for the same reasons regardless of mode
    (session missing, wrong state, data inconsistency).
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
                f"(state={session_row.state.value}); "
                f"cannot generate {artefact_label}."
        )
        campaign_context = session_row.campaign_context

        edited_source = _edited_transcript_source(session_row)
        if edited_source is not None:
            return edited_source, campaign_context

        if session_row.transcription_mode is TranscriptionMode.NON_DIARISED:
            chunks = await ChunkRepository(db).list_for_session(session_id)
            if not chunks:
                raise PermanentJobError(
                    f"Session {session_id} has no chunks; cannot generate "
                    f"{artefact_label}."
                )
            missing = [c.ordre for c in chunks if not c.summary_text]
            if missing:
                raise PermanentJobError(
                    f"Session {session_id} has chunks without summary_text "
                    f"(ordre={missing}); run POST /artifacts/summary first."
                )
            source_text = _SUMMARY_CHUNK_SEPARATOR.join(
                c.summary_text or "" for c in chunks
            )
            return source_text, campaign_context

        # diarised — Jalon 5 path
        transcription = await db.scalar(
            select(Transcription).where(Transcription.session_id == session_id)
        )
        if transcription is None:
            raise PermanentJobError(
                f"Session {session_id} has no transcription row "
                "even though state is 'transcribed' — data inconsistency."
            )
        segments = list(transcription.segments_json or [])
        return _format_segments_for_narrative(segments), campaign_context


async def _generate_narrative(session_id: UUID) -> None:
    """Build a French narrative summary of a transcribed session.

    Refuses to run if the session is not yet ``transcribed`` (PermanentJobError).
    Maps adapter errors to job errors so the RQ retry policy still applies.

    Mode-aware (feature 002): in `non_diarised` mode, consumes
    chunks.summary_text via :func:`_load_session_source_document`.
    """
    source_text, campaign_context = await _load_session_source_document(
        session_id, artefact_label="narrative"
    )
    sessionmaker = get_sessionmaker()

    # Step 2: build the user prompt from source document
    user_prompt = _build_user_prompt_with_context(
        campaign_context, source_text
    )

    # Step 3: call the LLM
    user_settings = await _load_session_model_settings(session_id)
    adapter = _build_llm_adapter_for_user(user_settings)
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
    model_used = _llm_model_used(adapter)
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

    Mode-aware (feature 002): in `non_diarised` mode, consumes
    chunks.summary_text via :func:`_load_session_source_document`.
    """
    source_text, campaign_context = await _load_session_source_document(
        session_id, artefact_label="elements"
    )
    sessionmaker = get_sessionmaker()

    user_prompt = _build_user_prompt_with_context(
        campaign_context, source_text
    )

    user_settings = await _load_session_model_settings(session_id)
    adapter = _build_llm_adapter_for_user(user_settings)
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

    # The LLM still returns the four canonical buckets; flatten them into the
    # category-tagged shape stored since BD-26 (npcs->PNJ, locations->Lieux,
    # items->Objets, clues->Indices).
    elements = {"elements": flatten_elements(_parse_elements_response(raw))}

    model_used = _llm_model_used(adapter)
    async with sessionmaker() as db:
        await ArtifactRepository(db).upsert(
            session_id,
            kind="elements",
            content_json=elements,
            model_used=model_used,
        )
        await db.commit()


async def _generate_povs(session_id: UUID) -> None:
    """Build one POV summary per declared PJ for a transcribed session.

    Mode-aware (feature 002):
    - **diarised** : reads `SessionPjMapping` rows and uses each
      `speaker_label → pj_id` to scope the POV. Behaviour Jalon 5.
    - **non_diarised** : reads `SessionPlayer` rows (list of pj_ids,
      no speaker_label) and asks the LLM to infer who acts from the
      context. Source document is `chunks.summary_text` joined.

    Both modes UPSERT one ``Artifact(kind='pov:<pj_id>')`` per declared PJ.
    """
    sessionmaker = get_sessionmaker()

    # Step 1: load + validate, fork on mode
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
        is_non_diarised = (
            session_row.transcription_mode is TranscriptionMode.NON_DIARISED
        )
        campaign_context = session_row.campaign_context

        # Build the list of (pj_id, speaker_label_or_None) pairs.
        pj_pairs: list[tuple[UUID, str | None]] = []
        if is_non_diarised:
            players = await SessionPlayerRepository(db).list_for_session(
                session_id
            )
            if not players:
                raise PermanentJobError(
                    f"Session {session_id} has no PJ declared via "
                    "POST /players; set one before generating POVs."
                )
            pj_pairs = [(p.pj_id, None) for p in players]
        else:
            mappings = await MappingRepository(db).get_for_session(session_id)
            if not mappings:
                raise PermanentJobError(
                    f"Session {session_id} has no speaker-PJ mapping; "
                    "set one via PUT /mapping before generating POVs."
                )
            pj_pairs = [(m.pj_id, m.speaker_label) for m in mappings]

        # Load PJ rows so we can put their names in the prompt.
        all_pj_ids = [pid for pid, _ in pj_pairs]
        pj_rows = (
            await db.execute(select(Pj).where(Pj.id.in_(all_pj_ids)))
        ).scalars().all()
        pj_by_id: dict[UUID, Pj] = {p.id: p for p in pj_rows}

    # Step 2: build the source document (modes-aware via helper)
    source_text, _ = await _load_session_source_document(
        session_id, artefact_label="povs"
    )

    # Step 3: per-PJ LLM calls
    user_settings = await _load_session_model_settings(session_id)
    adapter = _build_llm_adapter_for_user(user_settings)
    model_used = _llm_model_used(adapter)

    results: list[tuple[UUID, str]] = []
    for pj_id, speaker_label in pj_pairs:
        pj = pj_by_id.get(pj_id)
        if pj is None:
            logger.warning(
                "pov.skip_unknown_pj",
                session_id=str(session_id),
                pj_id=str(pj_id),
            )
            continue
        user_prompt = _build_pov_user_prompt(
            pj_name=pj.name,
            speaker_label=speaker_label or "(non-diarised : aucun label)",
            transcript_block=source_text,
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

    Step 0: load + validate (mode, state, source text).
    Step 1 (map): one LLM call per automatic or transient edited chunk, in
        `ordre` ASC, collected in memory. No DB transaction is held during the
        LLM phase (research.md §2).
    Step 2 (reduce): if > 1 chunk, consolidate the partial summaries
        via one more LLM call. Otherwise the single partial summary is
        used as-is.
    Step 3 (atomic swap, cascade FR-011): in ONE transaction — reset
        chunks.summary_text, write the new per-chunk summaries, DELETE
        artifacts(kind IN ('narrative','elements') OR kind LIKE 'pov:%'), and
        UPSERT artifacts(kind='summary'). Runs ONLY after the LLM succeeded, so
        a failed regeneration (Story 7.4) never destroys the prior summary or
        derived artifacts, and no destructive write precedes the new summary.
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
        edited_source = _edited_transcript_source(session_row)
        uses_edited_source = edited_source is not None
        if uses_edited_source:
            edited_chunks = chunk_text(
                edited_source,
                max_chars=settings.KAEYRIS_CHUNK_MAX_CHARS,
            )
            if not edited_chunks:
                raise PermanentJobError(
                    f"Session {session_id} has a blank edited transcription; "
                    "cannot generate summary."
                )
            chunk_data = [
                (None, ordre, text) for ordre, text in enumerate(edited_chunks)
            ]
        else:
            chunks = await ChunkRepository(db).list_for_session(session_id)
            if not chunks:
                raise PermanentJobError(
                    f"Session {session_id} has no chunks; cannot generate summary."
                )
            # Capture light projection (id, ordre, text) so we don't hold ORM
            # objects across DB sessions.
            chunk_data = [(c.id, c.ordre, c.text) for c in chunks]

    # --- Step 1 : map (LLM only — NO DB transaction held) -------------------
    # Story 7.4 / FR-011 fix: the destructive cascade (reset summary_text +
    # delete derived artifacts) used to run BEFORE the LLM. A failed regen
    # (401, transient, rate-limit…) then destroyed the prior summary and left a
    # misleading downstream "run summary first". The map+reduce now run first,
    # purely in memory; nothing is written/deleted until the new summary exists.
    user_settings = await _load_session_model_settings(session_id)
    adapter = _build_llm_adapter_for_user(user_settings)
    partial_summaries: list[str] = []
    for _chunk_id, _ordre, text in chunk_data:
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

    # --- Step 2 : reduce ----------------------------------------------------
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

    # --- Step 3 : atomic swap (cascade FR-011) — ONLY after the LLM succeeded -
    # reset chunk summaries + write the new ones + delete stale derived
    # artifacts + upsert the new summary, in ONE transaction. No LLM call is
    # held inside, so DB locks never span the long LLM phase (research.md §2),
    # and a failed regen above leaves all prior data untouched.
    from sqlalchemy import delete as sa_delete

    from app.services.jdr.db.models import Artifact as ArtifactModel

    model_used = _llm_model_used(adapter)
    async with sessionmaker() as db:
        await ChunkRepository(db).reset_summary_texts(session_id)
        for (chunk_id, _ordre, _text), partial in zip(
            chunk_data, partial_summaries
        ):
            if chunk_id is not None:
                await ChunkRepository(db).update_summary_text(
                    chunk_id, summary_text=partial
                )
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
    on_progress: Callable[[int, int], None] | None = None,
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

    ``on_progress`` (BD-10) is an optional ``(chunks_done, chunks_total)``
    callback invoked after each chunk is transcribed. It is queue-agnostic
    on purpose — the helper owns the real denominator but knows nothing
    about RQ; the job boundary maps these events to job metadata. The
    single-shot path emits no callback (no chunk denominator to report).

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
        chunks_total = len(chunks)
        for index, (offset, chunk_path) in enumerate(chunks):
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
            if on_progress is not None:
                on_progress(index + 1, chunks_total)

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
            raw_excerpt=raw[:200],
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


def _data_dir() -> Path:
    return Path(settings.KAEYRIS_DATA_DIR)


def _relative_data_path(path: Path) -> str:
    return path.relative_to(_data_dir()).as_posix()


def _prepared_audio_path_for_session(session_id: UUID) -> Path:
    return _data_dir() / "audios" / f"{session_id}.m4a"


def _transient_audio_dir(session_id: UUID) -> Path:
    return _data_dir() / ".tmp" / "audio-reduce" / str(session_id)


def _cleanup_transient_audio_dir(session_id: UUID) -> None:
    path = _transient_audio_dir(session_id)
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError as exc:
        logger.warning(
            "audio.reduce_cleanup_failed",
            session_id=str(session_id),
            path=str(path),
            error=str(exc),
        )


def _is_transient_raw_audio_path(audio_path_relative: str) -> bool:
    normalized = audio_path_relative.replace("\\", "/")
    return normalized.startswith(".tmp/audio-reduce/") and normalized.endswith(
        "/raw.m4a"
    )


async def _mark_session_failed(
    sessionmaker,
    session_id: UUID,
    *,
    failure_reason: str | None = None,
) -> None:
    async with sessionmaker() as db:
        session_row = await db.get(Session, session_id)
        current_job_id = session_row.current_job_id if session_row is not None else None
        await db.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(state=SessionState.TRANSCRIPTION_FAILED)
        )
        if current_job_id is not None:
            await db.execute(
                update(Job)
                .where(Job.id == current_job_id)
                .values(
                    status=JobStatus.FAILED,
                    failure_reason=failure_reason,
                )
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
