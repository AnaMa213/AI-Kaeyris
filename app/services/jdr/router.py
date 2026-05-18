"""Top-level router for the JDR service.

Mounted at ``/services/jdr`` (CLAUDE.md §4.2). Every route attached
here inherits the default dependencies declared on the router:

- ``require_api_key`` enforces a valid Bearer token (jalon 2).
- ``enforce_rate_limit`` applies a per-key sliding window (jalon 3).

Role-based authorisation is expressed at the route level via
``Depends(require_gm)`` / ``Depends(require_player)``. Both extend
``require_api_key`` (FastAPI caches the dependency per request).
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from redis import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    AuthenticatedKey,
    ForbiddenError,
    UnauthorizedError,
    require_api_key,
    require_gm,
    require_player,
)
from app.core.db import get_db_session
from app.core.errors import AppError
from app.core.rate_limit import enforce_rate_limit
from app.core.redis_client import get_redis
from app.jobs import enqueue_job, get_default_queue
from app.jobs.jdr import (
    generate_elements_job,
    generate_narrative_job,
    generate_povs_job,
)
from rq.exceptions import NoSuchJobError
from rq.job import Job
from app.services.jdr import logic
from app.services.jdr.batch.router import router as batch_router
from app.services.jdr.live.router import router as live_router
from app.services.jdr.logic import DuplicatePjError, InvalidPlayerError
from app.services.jdr.db.models import (
    JobKind,
    JobStatus,
    Session as SessionModel,
    SessionState,
    TranscriptionMode,
)
from app.services.jdr.db.repositories import (
    ArtifactRepository,
    MappingRepository,
    PjRepository,
    TranscriptionRepository,
)
from app.services.jdr.markdown import (
    render_elements_md,
    render_narrative_md,
    render_pov_md,
    render_transcription_md,
)
from app.services.jdr.schemas import (
    ChunkListOut,
    ChunkOut,
    Element,
    ElementsArtifactOut,
    JobOut,
    JobQueuedOut,
    MappingOut,
    MappingPut,
    MeOut,
    NarrativeArtifactOut,
    Page,
    PjCreate,
    PjMini,
    PjOut,
    PlayerCreate,
    PlayerOut,
    PlayerSessionItem,
    PlayerSessionListOut,
    PovArtifactOut,
    SessionCreate,
    SessionOut,
    SessionUpdate,
    TranscriptionOut,
    TranscriptionSegmentOut,
)


class SessionNotFoundError(AppError):
    """Returned when a session does not exist or does not belong to the GM."""

    status_code = status.HTTP_404_NOT_FOUND
    error_type = "session-not-found"
    title = "Session not found"


class TranscriptionNotReadyError(AppError):
    """Session exists but the transcription has not been produced yet."""

    status_code = status.HTTP_404_NOT_FOUND
    error_type = "transcription-not-ready"
    title = "Transcription not ready"


class ArtifactNotReadyError(AppError):
    """Session exists but the requested artefact has not been generated yet."""

    status_code = status.HTTP_404_NOT_FOUND
    error_type = "artifact-not-ready"
    title = "Artifact not ready"


class SessionNotTranscribedError(AppError):
    """Triggering an artefact requires the session to be transcribed first."""

    status_code = status.HTTP_409_CONFLICT
    error_type = "session-not-transcribed"
    title = "Session not transcribed"


class JobNotFoundError(AppError):
    """Job id unknown, expired from Redis, or belongs to another GM."""

    status_code = status.HTTP_404_NOT_FOUND
    error_type = "job-not-found"
    title = "Job not found"


class DuplicatePjConflictError(AppError):
    """A PJ with this name already exists for this MJ (uniqueness violated)."""

    status_code = status.HTTP_409_CONFLICT
    error_type = "duplicate-pj"
    title = "Duplicate PJ name"


class InvalidMappingError(AppError):
    """One or more ``pj_id`` in the mapping body is unknown or owned by another MJ."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    error_type = "invalid-mapping"
    title = "Invalid mapping"


class NoMappingError(AppError):
    """POV generation requires a configured speaker-PJ mapping first (FR-011)."""

    status_code = status.HTTP_409_CONFLICT
    error_type = "no-mapping"
    title = "Missing speaker-PJ mapping"


class PjNotFoundError(AppError):
    """Returned when a PJ does not exist or does not belong to the current MJ."""

    status_code = status.HTTP_404_NOT_FOUND
    error_type = "pj-not-found"
    title = "PJ not found"


class InvalidPlayerEnrolmentError(AppError):
    """The PJ referenced in a player enrolment is unknown or owned by another MJ."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    error_type = "invalid-player"
    title = "Invalid player enrolment"


class PlayerNotFoundError(AppError):
    """Returned when a player key does not exist or is not owned by the current MJ."""

    status_code = status.HTTP_404_NOT_FOUND
    error_type = "player-not-found"
    title = "Player not found"


class PlayerForbiddenError(AppError):
    """The player's PJ is not mapped on this session — FR-014 hard wall."""

    status_code = status.HTTP_403_FORBIDDEN
    error_type = "player-forbidden"
    title = "Forbidden — your PJ is not mapped on this session"


# --- Feature 002 (non_diarised mode) -----------------------------------------


class WrongModeError(AppError):
    """The endpoint is incompatible with the session's transcription_mode.

    Examples: /chunks on a diarised session, /transcription on a
    non_diarised session, /mapping on non_diarised, /players on diarised.
    """

    status_code = status.HTTP_409_CONFLICT
    error_type = "wrong-mode"
    title = "Wrong transcription mode"


class ImmutableFieldError(AppError):
    """A PATCH body tried to modify a field that is immutable after creation."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    error_type = "immutable-field"
    title = "Immutable field"


# Function name -> JobKind mapping. RQ pickles the callable by its module
# path; we use the inverse to expose a stable enum in JobOut.
_FUNC_NAME_TO_KIND: dict[str, JobKind] = {
    "app.jobs.jdr.transcribe_session_job": JobKind.TRANSCRIPTION,
    "app.jobs.jdr.generate_narrative_job": JobKind.NARRATIVE,
    "app.jobs.jdr.generate_elements_job": JobKind.ELEMENTS,
    "app.jobs.jdr.generate_povs_job": JobKind.POVS,
}

# RQ status (string) -> our coarser JobStatus.
_RQ_STATUS_TO_JOB_STATUS: dict[str, JobStatus] = {
    "queued": JobStatus.QUEUED,
    "deferred": JobStatus.QUEUED,
    "scheduled": JobStatus.QUEUED,
    "started": JobStatus.RUNNING,
    "finished": JobStatus.SUCCEEDED,
    "failed": JobStatus.FAILED,
    "stopped": JobStatus.FAILED,
    "canceled": JobStatus.FAILED,
}


def _ensure_aware(dt):
    """RQ stores naive UTC datetimes; expose them as timezone-aware."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


# The class above is exported for tests and future error handling; the
# unused-import linter would otherwise prune the reference.
_ = UnauthorizedError


router = APIRouter(
    prefix="/services/jdr",
    tags=["jdr"],
    dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)],
)

# Sub-routers — each adds its own routes; auth/rate-limit are inherited.
router.include_router(batch_router)
router.include_router(live_router)


# ---------------------------------------------------------------------------
# Sessions (US1)
# ---------------------------------------------------------------------------


@router.post(
    "/sessions",
    response_model=SessionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new JDR session.",
)
async def create_session(
    payload: SessionCreate,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SessionOut:
    """Create a session owned by the authenticated MJ.

    The body holds the human title and the date the session actually
    took place (``recorded_at``). State starts at ``created``; the
    audio is uploaded separately via ``POST /sessions/{id}/audio``.
    The optional ``campaign_context`` is a steering block for the LLM
    (PNJ récurrents, ton, fil narratif) — see PATCH for updating it.
    """
    row = await logic.create_session(
        db,
        title=payload.title,
        recorded_at=payload.recorded_at,
        gm_key_id=auth.id,
        campaign_context=payload.campaign_context,
        transcription_mode=(
            payload.transcription_mode
            if payload.transcription_mode is not None
            else TranscriptionMode.DIARISED
        ),
    )
    return SessionOut.model_validate(row)


@router.get(
    "/sessions",
    response_model=Page[SessionOut],
    summary="List the MJ's sessions.",
)
async def list_sessions(
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Page[SessionOut]:
    rows = await logic.list_sessions(db, gm_key_id=auth.id)
    items = [SessionOut.model_validate(r) for r in rows]
    return Page[SessionOut](items=items, total=len(items), page=1, size=len(items) or 1)


@router.get(
    "/sessions/{session_id}",
    response_model=SessionOut,
    summary="Fetch one of the MJ's sessions.",
)
async def get_session(
    session_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SessionOut:
    row = await logic.get_session(
        db, session_id=session_id, gm_key_id=auth.id
    )
    if row is None:
        raise SessionNotFoundError(
            detail=f"Session {session_id} not found."
        )
    return SessionOut.model_validate(row)


@router.patch(
    "/sessions/{session_id}",
    response_model=SessionOut,
    summary="Partially update a session (title and/or campaign_context).",
)
async def patch_session(
    session_id: UUID,
    request: Request,
    payload: SessionUpdate,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SessionOut:
    """Apply a partial update.

    Only the fields present in the body are touched. To clear
    ``campaign_context``, send ``{"campaign_context": null}`` explicitly —
    omitting the key leaves the existing value alone. Updating
    ``campaign_context`` does NOT re-run any previously generated
    artefact; the new value affects only future generations.

    ``transcription_mode`` is immutable after creation (FR-002 of
    feature 002): any PATCH body referencing it is rejected with 422.
    """
    # Inspect the raw body to detect immutable fields. SessionUpdate does
    # not declare `transcription_mode`, so Pydantic ignores it silently;
    # we need the raw JSON to detect the violation.
    raw_body = await request.json()
    if isinstance(raw_body, dict) and "transcription_mode" in raw_body:
        raise ImmutableFieldError(
            detail="transcription_mode is immutable after session creation.",
        )

    session = await logic.get_session(
        db, session_id=session_id, gm_key_id=auth.id
    )
    if session is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")

    fields_set = payload.model_fields_set
    updated = await logic.update_session(
        db,
        session=session,
        title=payload.title if "title" in fields_set else None,
        campaign_context=payload.campaign_context,
        set_campaign_context="campaign_context" in fields_set,
    )
    return SessionOut.model_validate(updated)


# ---------------------------------------------------------------------------
# Chunks (feature 002 — non_diarised mode)
# ---------------------------------------------------------------------------


@router.get(
    "/sessions/{session_id}/chunks",
    response_model=ChunkListOut,
    summary="Chunked transcription of a non_diarised session (ordered).",
)
async def get_session_chunks(
    session_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ChunkListOut:
    """Return the chunks (text, ordre) of a non_diarised session.

    Reserved for non_diarised sessions — diarised sessions use the
    `GET /transcription` endpoint instead (409 wrong-mode otherwise).
    Returns 404 transcription-not-ready if no chunks have been produced
    yet for the session.
    """
    session = await logic.get_session(
        db, session_id=session_id, gm_key_id=auth.id
    )
    if session is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    if session.transcription_mode is not TranscriptionMode.NON_DIARISED:
        raise WrongModeError(
            detail=(
                f"Session {session_id} is in mode 'diarised'. "
                "Use GET /transcription for this session."
            ),
        )
    chunks = await logic.list_session_chunks(db, session=session)
    if not chunks:
        raise TranscriptionNotReadyError(
            detail=(
                f"Transcription chunks for session {session_id} are not "
                "available yet."
            ),
        )
    return ChunkListOut(
        session_id=session.id,
        items=[ChunkOut.model_validate(c) for c in chunks],
    )


# ---------------------------------------------------------------------------
# PJ — Personnages-joueurs (US3 — sub-lot 5a)
# ---------------------------------------------------------------------------


@router.post(
    "/pjs",
    response_model=PjOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a PJ (player-character) owned by the current MJ.",
)
async def create_pj(
    payload: PjCreate,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> PjOut:
    """A PJ is stable across sessions — it's the narrative anchor that
    later gets mapped to a diarisation ``speaker_label`` per session
    (sub-lot 5b) and to a player API key (US4).

    Duplicate name within the same MJ -> 409 ``duplicate-pj``.
    """
    try:
        pj = await logic.create_pj(db, name=payload.name, gm_key_id=auth.id)
    except DuplicatePjError as exc:
        raise DuplicatePjConflictError(detail=str(exc)) from exc
    return PjOut.model_validate(pj)


@router.get(
    "/pjs",
    response_model=Page[PjOut],
    summary="List the current MJ's PJs.",
)
async def list_pjs(
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Page[PjOut]:
    rows = await logic.list_pjs(db, gm_key_id=auth.id)
    items = [PjOut.model_validate(r) for r in rows]
    return Page[PjOut](items=items, total=len(items), page=1, size=len(items) or 1)


# ---------------------------------------------------------------------------
# Speaker ↔ PJ mapping (US3 — sub-lot 5a)
# ---------------------------------------------------------------------------


@router.put(
    "/sessions/{session_id}/mapping",
    response_model=MappingOut,
    summary="Replace the speaker→PJ mapping for a session.",
)
async def put_session_mapping(
    session_id: UUID,
    payload: MappingPut,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> MappingOut:
    """Replace the mapping in one atomic write.

    - 404 if the session does not belong to the current MJ.
    - 409 if the session is not yet in ``state=transcribed``.
    - 422 if any ``pj_id`` is unknown or owned by another MJ.

    Side-effect: any existing ``pov:*`` artefact for this session is
    deleted (data-model.md §6 invariant). A subsequent
    ``POST /artifacts/povs`` is required to regenerate them.
    """
    session = await logic.get_session(
        db, session_id=session_id, gm_key_id=auth.id
    )
    if session is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    if session.state != SessionState.TRANSCRIBED:
        raise SessionNotTranscribedError(
            detail=(
                f"Session {session_id} is in state '{session.state.value}'; "
                "mapping requires 'transcribed'."
            )
        )
    try:
        result = await logic.set_session_mapping(
            db,
            session=session,
            mapping=payload.mapping,
            gm_key_id=auth.id,
        )
    except logic.InvalidMappingError as exc:
        raise InvalidMappingError(detail=str(exc)) from exc
    return MappingOut(
        session_id=session.id,
        mapping=result.mapping,
        updated_at=result.updated_at,
    )


@router.get(
    "/sessions/{session_id}/mapping",
    response_model=MappingOut,
    summary="Read the current speaker→PJ mapping for a session.",
)
async def get_session_mapping(
    session_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> MappingOut:
    """Returns 200 with an empty dict when the session has no mapping
    yet (resource exists but is not configured)."""
    session = await logic.get_session(
        db, session_id=session_id, gm_key_id=auth.id
    )
    if session is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    result = await logic.get_session_mapping(db, session_id=session.id)
    return MappingOut(
        session_id=session.id,
        mapping=result.mapping,
        updated_at=result.updated_at,
    )


# ---------------------------------------------------------------------------
# Transcription (US1 — sub-lot 3c)
# ---------------------------------------------------------------------------


@router.get(
    "/sessions/{session_id}/transcription",
    response_model=TranscriptionOut,
    summary="Fetch the diarised transcription of a session.",
)
async def get_transcription(
    session_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> TranscriptionOut:
    session = await logic.get_session(
        db, session_id=session_id, gm_key_id=auth.id
    )
    if session is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    if session.transcription_mode is TranscriptionMode.NON_DIARISED:
        raise WrongModeError(
            detail=(
                f"Session {session_id} is in mode 'non_diarised'. "
                "Use GET /chunks for this session."
            ),
        )

    transcription = await TranscriptionRepository(db).get_for_session(session_id)
    if transcription is None:
        raise TranscriptionNotReadyError(
            detail=(
                f"Transcription for session {session_id} is not available yet. "
                "Poll the job status or wait for the worker to finish."
            ),
        )

    return TranscriptionOut(
        session_id=transcription.session_id,
        segments=[
            TranscriptionSegmentOut(**seg) for seg in transcription.segments_json
        ],
        language=transcription.language,
        model_used=transcription.model_used,
        provider=transcription.provider,
        completed_at=transcription.completed_at,
    )


@router.get(
    "/sessions/{session_id}/transcription.md",
    response_class=Response,
    summary="Export the transcription as Markdown (text/markdown).",
)
async def get_transcription_md(
    session_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    session = await logic.get_session(
        db, session_id=session_id, gm_key_id=auth.id
    )
    if session is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    if session.transcription_mode is TranscriptionMode.NON_DIARISED:
        raise WrongModeError(
            detail=(
                f"Session {session_id} is in mode 'non_diarised'. "
                "Use GET /chunks for this session."
            ),
        )
    transcription = await TranscriptionRepository(db).get_for_session(session_id)
    if transcription is None:
        raise TranscriptionNotReadyError(
            detail=(
                f"Transcription for session {session_id} is not available yet."
            ),
        )

    md = render_transcription_md(session, transcription)
    return Response(content=md, media_type="text/markdown; charset=utf-8")


# ---------------------------------------------------------------------------
# Narrative artifact (US1 — sub-lot 3d)
# ---------------------------------------------------------------------------


@router.post(
    "/sessions/{session_id}/artifacts/narrative",
    response_model=JobQueuedOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger the narrative summary generation for a session.",
)
async def post_narrative(
    session_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    redis_client: Annotated[Redis, Depends(get_redis)],
) -> JobQueuedOut:
    session_row = await logic.get_session(
        db, session_id=session_id, gm_key_id=auth.id
    )
    if session_row is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    if session_row.state != SessionState.TRANSCRIBED:
        raise SessionNotTranscribedError(
            detail=(
                f"Session {session_id} is in state {session_row.state.value!r}; "
                "narrative generation requires 'transcribed'."
            ),
        )

    queue = get_default_queue(redis_client)
    job = enqueue_job(
        queue, generate_narrative_job, session_id, transient_errors=True
    )
    return JobQueuedOut(
        id=job.id,
        kind=JobKind.NARRATIVE,
        session_id=session_id,
        status=JobStatus.QUEUED,
        queued_at=datetime.now(UTC),
    )


@router.get(
    "/sessions/{session_id}/artifacts/narrative",
    response_model=NarrativeArtifactOut,
    summary="Fetch the narrative summary of a session.",
)
async def get_narrative(
    session_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> NarrativeArtifactOut:
    session_row = await logic.get_session(
        db, session_id=session_id, gm_key_id=auth.id
    )
    if session_row is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")

    artifact = await ArtifactRepository(db).get(session_id, "narrative")
    if artifact is None:
        raise ArtifactNotReadyError(
            detail=(
                f"Narrative for session {session_id} has not been generated yet. "
                "POST to this endpoint to enqueue the job."
            ),
        )

    return NarrativeArtifactOut(
        session_id=artifact.session_id,
        text=str(artifact.content_json.get("text", "")),
        model_used=artifact.model_used,
        generated_at=artifact.generated_at,
    )


@router.get(
    "/sessions/{session_id}/artifacts/narrative.md",
    response_class=Response,
    summary="Export the narrative summary as Markdown (text/markdown).",
)
async def get_narrative_md(
    session_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    session = await logic.get_session(
        db, session_id=session_id, gm_key_id=auth.id
    )
    if session is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    artifact = await ArtifactRepository(db).get(session_id, "narrative")
    if artifact is None:
        raise ArtifactNotReadyError(
            detail=(
                f"Narrative for session {session_id} has not been generated yet."
            ),
        )

    md = render_narrative_md(session, artifact)
    return Response(content=md, media_type="text/markdown; charset=utf-8")


# ---------------------------------------------------------------------------
# Elements artifact (US2 — Lot 4)
# ---------------------------------------------------------------------------


@router.post(
    "/sessions/{session_id}/artifacts/elements",
    response_model=JobQueuedOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger the structured-elements card generation for a session.",
)
async def post_elements(
    session_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    redis_client: Annotated[Redis, Depends(get_redis)],
) -> JobQueuedOut:
    session_row = await logic.get_session(
        db, session_id=session_id, gm_key_id=auth.id
    )
    if session_row is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    if session_row.state != SessionState.TRANSCRIBED:
        raise SessionNotTranscribedError(
            detail=(
                f"Session {session_id} is in state {session_row.state.value!r}; "
                "elements generation requires 'transcribed'."
            ),
        )

    queue = get_default_queue(redis_client)
    job = enqueue_job(
        queue, generate_elements_job, session_id, transient_errors=True
    )
    return JobQueuedOut(
        id=job.id,
        kind=JobKind.ELEMENTS,
        session_id=session_id,
        status=JobStatus.QUEUED,
        queued_at=datetime.now(UTC),
    )


@router.get(
    "/sessions/{session_id}/artifacts/elements",
    response_model=ElementsArtifactOut,
    summary="Fetch the structured-elements card of a session.",
)
async def get_elements(
    session_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ElementsArtifactOut:
    session_row = await logic.get_session(
        db, session_id=session_id, gm_key_id=auth.id
    )
    if session_row is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")

    artifact = await ArtifactRepository(db).get(session_id, "elements")
    if artifact is None:
        raise ArtifactNotReadyError(
            detail=(
                f"Elements for session {session_id} have not been generated yet. "
                "POST to this endpoint to enqueue the job."
            ),
        )

    content = artifact.content_json or {}

    def _coerce(key: str) -> list[Element]:
        entries = content.get(key) or []
        return [
            Element(
                name=str(e.get("name", "")).strip(),
                description=str(e.get("description", "")).strip(),
            )
            for e in entries
            if isinstance(e, dict) and str(e.get("name", "")).strip()
        ]

    return ElementsArtifactOut(
        session_id=artifact.session_id,
        npcs=_coerce("npcs"),
        locations=_coerce("locations"),
        items=_coerce("items"),
        clues=_coerce("clues"),
        model_used=artifact.model_used,
        generated_at=artifact.generated_at,
    )


@router.get(
    "/sessions/{session_id}/artifacts/elements.md",
    response_class=Response,
    summary="Export the structured-elements card as Markdown (text/markdown).",
)
async def get_elements_md(
    session_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    session = await logic.get_session(
        db, session_id=session_id, gm_key_id=auth.id
    )
    if session is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    artifact = await ArtifactRepository(db).get(session_id, "elements")
    if artifact is None:
        raise ArtifactNotReadyError(
            detail=(
                f"Elements for session {session_id} have not been generated yet."
            ),
        )

    md = render_elements_md(session, artifact)
    return Response(content=md, media_type="text/markdown; charset=utf-8")


# ---------------------------------------------------------------------------
# POV artefacts (US3 — sub-lot 5b)
# ---------------------------------------------------------------------------


@router.post(
    "/sessions/{session_id}/artifacts/povs",
    response_model=JobQueuedOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger POV generation — one artefact per mapped PJ.",
)
async def post_povs(
    session_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    redis_client: Annotated[Redis, Depends(get_redis)],
) -> JobQueuedOut:
    """Enqueue a single job that generates one ``pov:<pj_id>`` artefact
    per row in the session's mapping. Pre-conditions match the other
    generators (404 if foreign, 409 if not transcribed) plus FR-011:
    a 409 ``no-mapping`` is raised when the session has no configured
    speaker-PJ mapping yet — the operator must call
    ``PUT /mapping`` first.
    """
    session_row = await logic.get_session(
        db, session_id=session_id, gm_key_id=auth.id
    )
    if session_row is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    if session_row.state != SessionState.TRANSCRIBED:
        raise SessionNotTranscribedError(
            detail=(
                f"Session {session_id} is in state {session_row.state.value!r}; "
                "POV generation requires 'transcribed'."
            ),
        )
    mappings = await MappingRepository(db).get_for_session(session_id)
    if not mappings:
        raise NoMappingError(
            detail=(
                f"Session {session_id} has no speaker-PJ mapping configured. "
                "Call PUT /services/jdr/sessions/{id}/mapping first."
            ),
        )

    queue = get_default_queue(redis_client)
    job = enqueue_job(
        queue, generate_povs_job, session_id, transient_errors=True
    )
    return JobQueuedOut(
        id=job.id,
        kind=JobKind.POVS,
        session_id=session_id,
        status=JobStatus.QUEUED,
        queued_at=datetime.now(UTC),
    )


async def _load_owned_pj_or_404(
    db: AsyncSession, *, pj_id: UUID, gm_key_id: UUID
):
    """Return the PJ row or raise ``PjNotFoundError`` (404).

    Both "doesn't exist" and "owned by another MJ" collapse to 404 so a
    MJ cannot probe the existence of foreign PJ ids.
    """
    pj = await PjRepository(db).find_by_id_owned_by(pj_id, gm_key_id)
    if pj is None:
        raise PjNotFoundError(detail=f"PJ {pj_id} not found.")
    return pj


@router.get(
    "/sessions/{session_id}/artifacts/povs/{pj_id_str}",
    summary="Fetch one PJ's POV — JSON, or Markdown via the .md suffix.",
)
async def get_pov(
    session_id: UUID,
    pj_id_str: str,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
):
    """One route handles both representations because the ``.md`` suffix
    pattern would otherwise collide with the UUID path param at the
    Starlette routing layer (``{pj_id}`` matches ``[^/]+`` which greedily
    swallows the ``.md``).

    - ``GET .../povs/<uuid>``     → JSON (:class:`PovArtifactOut`)
    - ``GET .../povs/<uuid>.md``  → Markdown (``text/markdown``)
    """
    as_md = pj_id_str.endswith(".md")
    pj_id_raw = pj_id_str[:-3] if as_md else pj_id_str
    try:
        pj_id = UUID(pj_id_raw)
    except ValueError as exc:
        raise PjNotFoundError(detail=f"PJ {pj_id_raw} not found.") from exc

    session_row = await logic.get_session(
        db, session_id=session_id, gm_key_id=auth.id
    )
    if session_row is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    pj = await _load_owned_pj_or_404(db, pj_id=pj_id, gm_key_id=auth.id)

    artifact = await ArtifactRepository(db).get(session_id, f"pov:{pj_id}")
    if artifact is None:
        raise ArtifactNotReadyError(
            detail=(
                f"POV for PJ {pj_id} in session {session_id} has not been "
                "generated yet. POST to /artifacts/povs to enqueue the job."
            ),
        )

    if as_md:
        md = render_pov_md(session_row, pj, artifact)
        return Response(content=md, media_type="text/markdown; charset=utf-8")

    content = artifact.content_json or {}
    text = str(content.get("text", "")) if isinstance(content, dict) else ""
    return PovArtifactOut(
        session_id=artifact.session_id,
        pj_id=pj_id,
        text=text,
        model_used=artifact.model_used,
        generated_at=artifact.generated_at,
    )


# ---------------------------------------------------------------------------
# Player enrolment + revocation (US4 — require_gm)
# ---------------------------------------------------------------------------


@router.post(
    "/players",
    response_model=PlayerOut,
    status_code=status.HTTP_201_CREATED,
    summary="Enroll a player and return the plaintext Bearer token (once).",
)
async def post_player(
    payload: PlayerCreate,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> PlayerOut:
    """Create a player key bound to one of the GM's PJs.

    The plaintext token is returned exactly once — store it now, the
    server only keeps the Argon2 hash.
    """
    try:
        result = await logic.enroll_player(
            db, name=payload.name, pj_id=payload.pj_id, gm_key_id=auth.id
        )
    except InvalidPlayerError as exc:
        raise InvalidPlayerEnrolmentError(detail=str(exc)) from exc
    return PlayerOut(
        id=result.api_key.id,
        name=result.api_key.name,
        pj_id=result.api_key.pj_id,
        token=result.plaintext_token,
        created_at=result.api_key.created_at,
    )


@router.delete(
    "/players/{player_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a player key (immediate effect on the next request).",
)
async def delete_player(
    player_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    revoked = await logic.revoke_player(
        db, player_id=player_id, gm_key_id=auth.id
    )
    if not revoked:
        raise PlayerNotFoundError(detail=f"Player {player_id} not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Player read endpoints (/me/* — require_player + FR-014 isolation)
# ---------------------------------------------------------------------------


async def _ensure_player_can_read_session(
    db: AsyncSession, *, session_id: UUID, player_pj_id: UUID | None
) -> None:
    """Block access unless the player's PJ is mapped on this session.

    A misconfigured player key (``pj_id is None``) is also blocked here
    even though auth should already reject it — defence in depth (FR-014).
    """
    if player_pj_id is None:
        raise PlayerForbiddenError(
            detail="Player key has no PJ bound — refuse by default."
        )
    mapped = await logic.is_pj_mapped_on_session(
        db, session_id=session_id, pj_id=player_pj_id
    )
    if not mapped:
        raise PlayerForbiddenError(
            detail=(
                f"Your PJ is not mapped on session {session_id}. "
                "Ask your MJ to update the mapping."
            ),
        )


@router.get(
    "/me",
    response_model=MeOut,
    summary="Profile of the current player (name + their PJ).",
)
async def get_me(
    auth: Annotated[AuthenticatedKey, Depends(require_player)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> MeOut:
    if auth.pj_id is None:
        # Defence in depth — auth should already have rejected this.
        raise ForbiddenError(detail="Player key has no PJ bound.")
    pj = await logic.get_player_pj(db, pj_id=auth.pj_id)
    if pj is None:
        raise PjNotFoundError(detail=f"PJ {auth.pj_id} not found.")
    return MeOut(name=auth.name, pj=PjMini.model_validate(pj))


@router.get(
    "/me/sessions",
    response_model=PlayerSessionListOut,
    summary="List the sessions where the current player's PJ is mapped.",
)
async def get_my_sessions(
    auth: Annotated[AuthenticatedKey, Depends(require_player)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> PlayerSessionListOut:
    if auth.pj_id is None:
        raise ForbiddenError(detail="Player key has no PJ bound.")
    sessions = await logic.list_player_sessions(db, player_pj_id=auth.pj_id)
    items = [
        PlayerSessionItem(
            session_id=s.id, title=s.title, recorded_at=s.recorded_at
        )
        for s in sessions
    ]
    return PlayerSessionListOut(items=items)


@router.get(
    "/me/sessions/{session_id}/narrative",
    response_model=NarrativeArtifactOut,
    summary="Read the global narrative summary of a session (player view).",
)
async def get_my_narrative(
    session_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_player)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> NarrativeArtifactOut:
    await _ensure_player_can_read_session(
        db, session_id=session_id, player_pj_id=auth.pj_id
    )
    artifact = await ArtifactRepository(db).get(session_id, "narrative")
    if artifact is None:
        raise ArtifactNotReadyError(
            detail=f"Narrative for session {session_id} has not been generated yet."
        )
    content = artifact.content_json or {}
    text = str(content.get("text", "")) if isinstance(content, dict) else ""
    return NarrativeArtifactOut(
        session_id=artifact.session_id,
        text=text,
        model_used=artifact.model_used,
        generated_at=artifact.generated_at,
    )


@router.get(
    "/me/sessions/{session_id}/narrative.md",
    response_class=Response,
    summary="Export the global narrative summary as Markdown (player view).",
)
async def get_my_narrative_md(
    session_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_player)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    await _ensure_player_can_read_session(
        db, session_id=session_id, player_pj_id=auth.pj_id
    )
    session = await db.scalar(
        select(SessionModel).where(SessionModel.id == session_id)
    )
    artifact = await ArtifactRepository(db).get(session_id, "narrative")
    if artifact is None or session is None:
        raise ArtifactNotReadyError(
            detail=f"Narrative for session {session_id} has not been generated yet."
        )
    md = render_narrative_md(session, artifact)
    return Response(content=md, media_type="text/markdown; charset=utf-8")


@router.get(
    "/me/sessions/{session_id}/pov",
    response_model=PovArtifactOut,
    summary="Read the current player's POV summary for a session.",
)
async def get_my_pov(
    session_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_player)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> PovArtifactOut:
    await _ensure_player_can_read_session(
        db, session_id=session_id, player_pj_id=auth.pj_id
    )
    artifact = await ArtifactRepository(db).get(session_id, f"pov:{auth.pj_id}")
    if artifact is None:
        raise ArtifactNotReadyError(
            detail=(
                f"POV for your PJ in session {session_id} has not been "
                "generated yet."
            )
        )
    content = artifact.content_json or {}
    text = str(content.get("text", "")) if isinstance(content, dict) else ""
    return PovArtifactOut(
        session_id=artifact.session_id,
        pj_id=auth.pj_id,
        text=text,
        model_used=artifact.model_used,
        generated_at=artifact.generated_at,
    )


@router.get(
    "/me/sessions/{session_id}/pov.md",
    response_class=Response,
    summary="Export the current player's POV as Markdown.",
)
async def get_my_pov_md(
    session_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_player)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    await _ensure_player_can_read_session(
        db, session_id=session_id, player_pj_id=auth.pj_id
    )
    session = await db.scalar(
        select(SessionModel).where(SessionModel.id == session_id)
    )
    pj = await logic.get_player_pj(db, pj_id=auth.pj_id)
    artifact = await ArtifactRepository(db).get(session_id, f"pov:{auth.pj_id}")
    if artifact is None or session is None or pj is None:
        raise ArtifactNotReadyError(
            detail=(
                f"POV for your PJ in session {session_id} has not been "
                "generated yet."
            )
        )
    md = render_pov_md(session, pj, artifact)
    return Response(content=md, media_type="text/markdown; charset=utf-8")


# ---------------------------------------------------------------------------
# Job status (US1 — sub-lot 3f)
# ---------------------------------------------------------------------------


@router.get(
    "/jobs/{job_id}",
    response_model=JobOut,
    summary="Fetch the status of a JDR-service async job.",
)
async def get_job(
    job_id: str,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    redis_client: Annotated[Redis, Depends(get_redis)],
) -> JobOut:
    """Project an RQ job into the project's JobOut shape.

    RQ holds the live state for 24h on success / 7d on failure. Cross-
    tenant access (a GM polling another GM's job) returns 404 so the
    endpoint never confirms the existence of a foreign job.
    """
    try:
        job = Job.fetch(job_id, connection=redis_client)
    except NoSuchJobError as exc:
        raise JobNotFoundError(detail=f"Job {job_id} not found.") from exc

    # Resolve kind from the function name. Unknown functions => 404, not 500.
    kind = _FUNC_NAME_TO_KIND.get(job.func_name)
    if kind is None:
        raise JobNotFoundError(
            detail=f"Job {job_id} is not a recognised JDR job."
        )

    # Pull the session_id from the first positional arg.
    args = job.args or ()
    if not args:
        raise JobNotFoundError(
            detail=f"Job {job_id} has no session_id argument."
        )
    raw_session_id = args[0]
    try:
        session_id = raw_session_id if isinstance(raw_session_id, UUID) else UUID(
            str(raw_session_id)
        )
    except (TypeError, ValueError) as exc:
        raise JobNotFoundError(
            detail=f"Job {job_id} has a malformed session_id."
        ) from exc

    # Cross-tenant guard: hide other MJ's jobs as if they didn't exist.
    session_row = await logic.get_session(
        db, session_id=session_id, gm_key_id=auth.id
    )
    if session_row is None:
        raise JobNotFoundError(detail=f"Job {job_id} not found.")

    rq_status = job.get_status(refresh=True)
    status_value = _RQ_STATUS_TO_JOB_STATUS.get(rq_status, JobStatus.FAILED)

    failure_reason: str | None = None
    if job.is_failed and getattr(job, "exc_info", None):
        # RQ keeps the full traceback; keep just the last line to avoid
        # leaking too much internal detail to the client.
        failure_reason = str(job.exc_info).strip().splitlines()[-1][:500]

    return JobOut(
        id=job.id,
        kind=kind,
        session_id=session_id,
        status=status_value,
        failure_reason=failure_reason,
        queued_at=_ensure_aware(job.created_at) or datetime.now(UTC),
        started_at=_ensure_aware(getattr(job, "started_at", None)),
        ended_at=_ensure_aware(getattr(job, "ended_at", None)),
    )
