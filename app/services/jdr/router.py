"""Top-level router for the JDR service.

Mounted at ``/services/jdr`` (CLAUDE.md §4.2). Every route attached
here inherits the default dependencies declared on the router:

- ``require_api_key`` enforces a valid Bearer token (jalon 2).
- ``enforce_rate_limit`` applies a per-key sliding window (jalon 3).

Role-based authorisation is expressed at the route level via
``Depends(require_gm)`` / ``Depends(require_player)``. Both extend
``require_api_key`` (FastAPI caches the dependency per request).
"""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse
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
from app.jobs.jdr import (
    generate_elements_job,
    generate_narrative_job,
    generate_povs_job,
    generate_summary_job,
)
from rq.exceptions import NoSuchJobError
from rq.job import Job
from app.services.jdr import logic
from app.services.jdr.batch.router import router as batch_router
from app.services.jdr.elements import elements_from_content
from app.services.jdr.campaign_context import (
    CampaignAccessError,
    resolve_campaign_scope_for_auth,
)
from app.services.jdr.live.router import router as live_router
from app.services.jdr.logic import (
    DuplicatePjError,
    InvalidPlayerError,
    InvalidPlayerListError as LogicInvalidPlayerListError,
    PjAssignmentError,
    PjCampaignResolutionError,
    PjForbiddenError,
)
from app.services.jdr.db.models import (
    JobKind,
    JobStatus,
    Session as SessionModel,
    SessionState,
    TranscriptionMode,
)
from app.services.jdr.db.repositories import (
    ArtifactRepository,
    CampaignRepository,
    MappingRepository,
    PjRepository,
    TranscriptionRepository,
)
from app.services.jdr.markdown import (
    render_elements_md,
    render_narrative_md,
    render_pov_md,
    render_summary_md,
    render_transcription_md,
)
from app.services.jdr.session_access import resolve_session_for_gm
from app.services.jdr.schemas import (
    CampaignCreate,
    CampaignOut,
    CampaignPatch,
    ChunkListOut,
    ChunkOut,
    Element,
    ElementsArtifactOut,
    ElementsPutIn,
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
    PjUpdate,
    PlayerCreate,
    PlayerOut,
    PlayerSessionItem,
    PlayerSessionListOut,
    PovArtifactOut,
    SessionCreate,
    SessionOut,
    SessionPlayersIn,
    SessionPlayersOut,
    SessionUpdate,
    SummaryArtifactOut,
    TextEditIn,
    TranscriptionEditIn,
    TranscriptionEditOut,
    TranscriptionOut,
    TranscriptionSegmentOut,
)


class SessionNotFoundError(AppError):
    """Returned when a session does not exist or does not belong to the GM."""

    status_code = status.HTTP_404_NOT_FOUND
    error_type = "session-not-found"
    title = "Session not found"


class SessionDeleteBlockedError(AppError):
    """Returned when a session still has active work and cannot be deleted."""

    status_code = status.HTTP_409_CONFLICT
    error_type = "session-delete-blocked"
    title = "Session delete blocked"


class TranscriptionNotStuckError(AppError):
    """Recovery requested but the session is not wedged in ``transcribing``."""

    status_code = status.HTTP_409_CONFLICT
    error_type = "transcription-not-stuck"
    title = "Transcription not stuck"


class TranscriptionStillActiveError(AppError):
    """Recovery refused because the transcription job is still running."""

    status_code = status.HTTP_409_CONFLICT
    error_type = "transcription-still-active"
    title = "Transcription still active"


class TranscriptionNotReadyError(AppError):
    """Session exists but the transcription has not been produced yet."""

    status_code = status.HTTP_404_NOT_FOUND
    error_type = "transcription-not-ready"
    title = "Transcription not ready"


class TranscriptionRestartNotAllowedError(AppError):
    """Re-transcription requested from a state that does not allow it."""

    status_code = status.HTTP_409_CONFLICT
    error_type = "transcription-restart-not-allowed"
    title = "Transcription restart not allowed"


class NoAudioToTranscribeError(AppError):
    """Re-transcription requested but the session has no usable audio source."""

    status_code = status.HTTP_409_CONFLICT
    error_type = "no-audio-to-transcribe"
    title = "No audio to transcribe"


class ArtifactNotReadyError(AppError):
    """Session exists but the requested artefact has not been generated yet."""

    status_code = status.HTTP_404_NOT_FOUND
    error_type = "artifact-not-ready"
    title = "Artifact not ready"


class ArtifactEditedAppError(AppError):
    """Regeneration would overwrite a manually edited artifact (BD-24)."""

    status_code = status.HTTP_409_CONFLICT
    error_type = "artifact-edited"
    title = "Artifact edited"


class SessionNotTranscribedError(AppError):
    """Triggering an artefact requires the session to be transcribed first."""

    status_code = status.HTTP_409_CONFLICT
    error_type = "session-not-transcribed"
    title = "Session not transcribed"


class TranscriptionEditNotTranscribedError(SessionNotTranscribedError):
    """Saving an edit requires an already completed transcription."""


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


class PjCampaignRequiredError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    error_type = "pj-campaign-required"
    title = "PJ campaign required"


class PjForbiddenAppError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    error_type = "pj-forbidden"
    title = "Forbidden"


class InvalidUserAssignmentError(AppError):
    """The ``user_id`` assigned to a PJ does not reference an existing user."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    error_type = "invalid-user"
    title = "Invalid user"


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


class CampaignNotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    error_type = "campaign-not-found"
    title = "Campaign not found"


class CampaignForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    error_type = "campaign-forbidden"
    title = "Forbidden"


class DuplicateCampaignConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    error_type = "duplicate-campaign"
    title = "Duplicate campaign name"


class CampaignDeleteConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    error_type = "campaign-has-sessions"
    title = "Campaign has sessions"


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


class NoChunksError(AppError):
    """Non_diarised session has no chunks — summary generation impossible."""

    status_code = status.HTTP_409_CONFLICT
    error_type = "no-chunks"
    title = "No chunks available"


class NoSummaryError(AppError):
    """Non_diarised session has no global summary yet — derived artefacts
    can't run. Hint: call POST /artifacts/summary first (FR-010)."""

    status_code = status.HTTP_409_CONFLICT
    error_type = "no-summary"
    title = "No session summary generated"


class InvalidPlayerListError(AppError):
    """One or more pj_id in the players body is unknown or owned by another MJ."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    error_type = "invalid-player-list"
    title = "Invalid player list"


# Function name -> JobKind mapping. RQ pickles the callable by its module
# path; we use the inverse to expose a stable enum in JobOut.
_FUNC_NAME_TO_KIND: dict[str, JobKind] = {
    "app.jobs.jdr.transcribe_session_job": JobKind.TRANSCRIPTION,
    "app.jobs.jdr.generate_narrative_job": JobKind.NARRATIVE,
    "app.jobs.jdr.generate_elements_job": JobKind.ELEMENTS,
    "app.jobs.jdr.generate_povs_job": JobKind.POVS,
    "app.jobs.jdr.generate_summary_job": JobKind.SUMMARY,
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


def _failure_reason_from_job(job: Job) -> str | None:
    if not job.is_failed:
        return None
    result = job.latest_result()
    exc_info = getattr(result, "exc_string", None) if result else None
    exc_info = exc_info or getattr(job, "_exc_info", None)
    if exc_info:
        # RQ stores the full traceback; expose only the last meaningful line.
        for line in reversed(str(exc_info).splitlines()):
            line = line.strip()
            if line:
                return line[:500]
    return "Job failed without a recorded error."


def _ensure_aware(dt):
    """RQ stores naive UTC datetimes; expose them as timezone-aware."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _has_edited_transcription(session_row: SessionModel) -> bool:
    return bool((session_row.edited_transcript_md or "").strip())


def _is_downstream_artifact(kind: str) -> bool:
    return kind in {"narrative", "elements"} or kind.startswith("pov:")


def _artifact_kind_label(kind: str) -> str:
    return "povs" if kind.startswith("pov:") else kind


async def _guard_regeneration_not_edited(
    db: AsyncSession,
    *,
    session_id: UUID,
    force: bool,
    target_kinds: tuple[str, ...] = (),
    include_povs: bool = False,
    include_downstream: bool = False,
) -> None:
    """Block destructive regeneration unless the caller explicitly confirms.

    The guard runs before enqueueing the job. The actual destructive write still
    happens inside the worker after model success, preserving Story 7.4's
    non-destructive failure semantics.
    """
    if force:
        return

    target_set = set(target_kinds)
    edited_kinds: list[str] = []
    for artifact in await ArtifactRepository(db).list_for_session(session_id):
        if not artifact.is_edited:
            continue
        kind = artifact.kind
        should_guard = (
            kind in target_set
            or (include_povs and kind.startswith("pov:"))
            or (include_downstream and _is_downstream_artifact(kind))
        )
        if should_guard:
            edited_kinds.append(kind)

    if edited_kinds:
        labels = ", ".join(
            sorted({_artifact_kind_label(kind) for kind in edited_kinds})
        )
        raise ArtifactEditedAppError(
            detail=(
                "Regeneration would overwrite manually edited artifact(s): "
                f"{labels}. Retry with ?force=true to confirm replacement."
            )
        )


# Closed phase vocabulary mirrored from JobOut (BD-10). Kept here so the route
# can reject anything else before it ever reaches Pydantic validation.
_VALID_JOB_PHASES = frozenset({"reducing", "transcribing", "done", "failed"})
_JOB_EVENTS_POLL_INTERVAL_SECONDS = 1.0
_TERMINAL_JOB_STATUSES = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED})
_JOB_NO_LONGER_AVAILABLE_REASON = "Job is no longer available."


def _project_progress_meta(meta: dict) -> tuple[str | None, int | None]:
    """Project best-effort RQ progress metadata into validated public fields.

    BD-10 invariant: progress is *best-effort*. Missing, expired, malformed,
    non-integer, or out-of-domain values collapse to ``None`` so they never
    turn a valid job into a 500 — they just hide the optional progress hint.
    ``queued`` jobs simply carry no metadata, so nothing is synthesised
    (no ``phase="queued"``, no ``progress_percent=0``).
    """
    raw_phase = meta.get("phase")
    phase = raw_phase if raw_phase in _VALID_JOB_PHASES else None

    raw_percent = meta.get("progress_percent")
    # bool is an int subclass — reject it explicitly so True/False is not 1/0.
    if isinstance(raw_percent, bool) or not isinstance(raw_percent, int):
        progress_percent = None
    elif 0 <= raw_percent <= 100:
        progress_percent = raw_percent
    else:
        progress_percent = None

    return phase, progress_percent


def _fetch_jdr_job(*, job_id: str, redis_client: Redis) -> Job:
    try:
        return Job.fetch(job_id, connection=redis_client)
    except NoSuchJobError as exc:
        raise JobNotFoundError(detail=f"Job {job_id} not found.") from exc


def _resolve_job_identity(*, job_id: str, job: Job) -> tuple[JobKind, UUID]:
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

    return kind, session_id


def _project_rq_job_out(*, job: Job, kind: JobKind, session_id: UUID) -> JobOut:
    rq_status = job.get_status(refresh=True)
    status_value = _RQ_STATUS_TO_JOB_STATUS.get(rq_status, JobStatus.FAILED)

    # BD-10: best-effort transcription progress lives on the RQ job metadata.
    # Validate it here so malformed/expired values fall back to null instead
    # of turning a valid job into a 500 (US2).
    meta = job.get_meta(refresh=True) or {}
    phase, progress_percent = _project_progress_meta(meta)

    return JobOut(
        id=job.id,
        kind=kind,
        session_id=session_id,
        status=status_value,
        failure_reason=_failure_reason_from_job(job),
        queued_at=_ensure_aware(job.created_at) or datetime.now(UTC),
        started_at=_ensure_aware(getattr(job, "started_at", None)),
        ended_at=_ensure_aware(getattr(job, "ended_at", None)),
        phase=phase,
        progress_percent=progress_percent,
    )


async def _project_job_out(
    *,
    job_id: str,
    auth: AuthenticatedKey,
    db: AsyncSession,
    redis_client: Redis,
) -> JobOut:
    """Project an RQ job into the public JobOut shape used by polling and SSE."""
    job = _fetch_jdr_job(job_id=job_id, redis_client=redis_client)
    kind, session_id = _resolve_job_identity(job_id=job_id, job=job)

    # Cross-tenant guard: hide other MJ's jobs as if they didn't exist.
    session_row = await resolve_session_for_gm(
        db, session_id=session_id, auth=auth
    )
    if session_row is None:
        raise JobNotFoundError(detail=f"Job {job_id} not found.")

    return _project_rq_job_out(job=job, kind=kind, session_id=session_id)


def _refresh_visible_job_out(
    *, job_id: str, kind: JobKind, session_id: UUID, redis_client: Redis
) -> JobOut:
    job = _fetch_jdr_job(job_id=job_id, redis_client=redis_client)
    return _project_rq_job_out(job=job, kind=kind, session_id=session_id)


def _job_no_longer_available_out(initial_job_out: JobOut) -> JobOut:
    return JobOut(
        id=initial_job_out.id,
        kind=initial_job_out.kind,
        session_id=initial_job_out.session_id,
        status=JobStatus.FAILED,
        failure_reason=_JOB_NO_LONGER_AVAILABLE_REASON,
        queued_at=initial_job_out.queued_at,
        started_at=initial_job_out.started_at,
        ended_at=datetime.now(UTC),
        phase=initial_job_out.phase,
        progress_percent=initial_job_out.progress_percent,
    )


def _job_event_payload(job_out: JobOut) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": job_out.status.value,
        "phase": job_out.phase,
        "progress_percent": job_out.progress_percent,
    }
    if job_out.status is JobStatus.FAILED and job_out.failure_reason:
        payload["failure_reason"] = job_out.failure_reason
    return payload


def _format_job_sse_frame(payload: dict[str, object]) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: progress\ndata: {data}\n\n"


async def _sleep_between_job_events(seconds: float) -> None:
    await asyncio.sleep(seconds)


async def _job_event_stream(
    *,
    initial_job_out: JobOut,
    job_id: str,
    redis_client: Redis,
) -> AsyncIterator[str]:
    job_out = initial_job_out
    while True:
        yield _format_job_sse_frame(_job_event_payload(job_out))
        if job_out.status in _TERMINAL_JOB_STATUSES:
            break
        await _sleep_between_job_events(_JOB_EVENTS_POLL_INTERVAL_SECONDS)
        try:
            job_out = _refresh_visible_job_out(
                job_id=job_id,
                kind=initial_job_out.kind,
                session_id=initial_job_out.session_id,
                redis_client=redis_client,
            )
        except JobNotFoundError:
            yield _format_job_sse_frame(
                _job_event_payload(_job_no_longer_available_out(initial_job_out))
            )
            break


async def _campaign_id_for_auth(
    db: AsyncSession,
    auth: AuthenticatedKey,
) -> UUID | None:
    scope = await resolve_campaign_scope_for_auth(db, auth)
    return scope.campaign_id if scope is not None else None


def _web_user_id(auth: AuthenticatedKey) -> UUID:
    if auth.source != "web_session" or auth.user_id is None:
        raise CampaignForbiddenError(detail="A web session is required.")
    return auth.user_id


def _campaign_out(summary) -> CampaignOut:
    return CampaignOut(
        id=summary.campaign.id,
        name=summary.campaign.name,
        description=summary.campaign.description,
        role=summary.role.value,
        session_count=summary.session_count,
        last_session_at=summary.last_session_at,
        created_at=summary.campaign.created_at,
    )


def _map_campaign_error(exc: Exception) -> AppError:
    if isinstance(exc, logic.CampaignNotFoundError):
        return CampaignNotFoundError(detail=str(exc))
    if isinstance(exc, logic.CampaignForbiddenError):
        return CampaignForbiddenError(detail=str(exc))
    if isinstance(exc, logic.DuplicateCampaignError):
        return DuplicateCampaignConflictError(detail=str(exc))
    if isinstance(exc, logic.CampaignHasSessionsError):
        return CampaignDeleteConflictError(detail=str(exc))
    raise exc


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
# Campaigns (BD-6)
# ---------------------------------------------------------------------------


@router.get(
    "/campaigns",
    response_model=Page[CampaignOut],
    summary="List the current web user's campaigns.",
)
async def list_campaigns(
    auth: Annotated[AuthenticatedKey, Depends(require_api_key)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Page[CampaignOut]:
    user_id = _web_user_id(auth)
    summaries = await logic.list_campaigns(db, user_id=user_id)
    items = [_campaign_out(summary) for summary in summaries]
    return Page[CampaignOut](items=items, total=len(items), page=1, size=50)


@router.post(
    "/campaigns",
    response_model=CampaignOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a campaign and make the current user its GM.",
)
async def create_campaign(
    payload: CampaignCreate,
    auth: Annotated[AuthenticatedKey, Depends(require_api_key)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> CampaignOut:
    user_id = _web_user_id(auth)
    try:
        summary = await logic.create_campaign(
            db,
            owner_user_id=user_id,
            name=payload.name,
            description=payload.description,
        )
    except Exception as exc:
        raise _map_campaign_error(exc) from exc
    return _campaign_out(summary)


@router.get(
    "/campaigns/{campaign_id}",
    response_model=CampaignOut,
    summary="Fetch one of the current web user's campaigns.",
)
async def get_campaign(
    campaign_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_api_key)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> CampaignOut:
    user_id = _web_user_id(auth)
    try:
        summary = await logic.get_campaign(
            db,
            campaign_id=campaign_id,
            user_id=user_id,
        )
    except Exception as exc:
        raise _map_campaign_error(exc) from exc
    return _campaign_out(summary)


@router.patch(
    "/campaigns/{campaign_id}",
    response_model=CampaignOut,
    summary="Partially update a campaign.",
)
async def patch_campaign(
    campaign_id: UUID,
    payload: CampaignPatch,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> CampaignOut:
    user_id = _web_user_id(auth)
    fields_set = payload.model_fields_set
    try:
        summary = await logic.update_campaign(
            db,
            campaign_id=campaign_id,
            user_id=user_id,
            name=payload.name if "name" in fields_set else None,
            description=payload.description,
            set_description="description" in fields_set,
        )
    except Exception as exc:
        raise _map_campaign_error(exc) from exc
    return _campaign_out(summary)


@router.delete(
    "/campaigns/{campaign_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an empty campaign.",
)
async def delete_campaign(
    campaign_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    user_id = _web_user_id(auth)
    try:
        await logic.delete_campaign(db, campaign_id=campaign_id, user_id=user_id)
    except Exception as exc:
        raise _map_campaign_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    if auth.user_id is not None:
        membership = await CampaignRepository(db).get_membership(
            user_id=auth.user_id,
            campaign_id=payload.campaign_id,
        )
        if membership is None or membership.role.value != "gm":
            raise CampaignForbiddenError(
                detail="This endpoint requires GM membership for the campaign."
            )
    row = await logic.create_session(
        db,
        title=payload.title,
        recorded_at=payload.recorded_at,
        gm_key_id=auth.id,
        campaign_id=payload.campaign_id,
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
    campaign_id: Annotated[UUID | None, Query()] = None,
) -> Page[SessionOut]:
    if campaign_id is not None and auth.user_id is not None:
        membership = await CampaignRepository(db).get_membership(
            user_id=auth.user_id,
            campaign_id=campaign_id,
        )
        if membership is None:
            raise CampaignForbiddenError(
                detail="User is not a member of this campaign."
            )
    rows = await logic.list_sessions(
        db, gm_key_id=auth.id, campaign_id=campaign_id
    )
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
        db,
        session_id=session_id,
        gm_key_id=auth.id,
        campaign_id=None,
    )
    if row is None:
        existing = await db.get(SessionModel, session_id)
        if (
            existing is not None
            and existing.campaign_id is not None
            and auth.user_id is not None
        ):
            membership = await CampaignRepository(db).get_membership(
                user_id=auth.user_id,
                campaign_id=existing.campaign_id,
            )
            if membership is None:
                raise CampaignForbiddenError(
                    detail="User is not a member of this campaign."
                )
        raise SessionNotFoundError(
            detail=f"Session {session_id} not found."
        )
    if row.campaign_id is not None and auth.user_id is not None:
        membership = await CampaignRepository(db).get_membership(
            user_id=auth.user_id,
            campaign_id=row.campaign_id,
        )
        if membership is None:
            raise CampaignForbiddenError(
                detail="User is not a member of this campaign."
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

    session = await resolve_session_for_gm(db, session_id=session_id, auth=auth)
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


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Session not found or not visible to the current GM."
        },
    },
    summary="Delete one of the MJ's sessions (aborts active work first).",
)
async def delete_session(
    session_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    redis_client: Annotated[Redis, Depends(get_redis)],
) -> Response:
    # Story 7.1 / BD-21: deletion now works in ANY state — an active job is
    # aborted first by the logic layer, so there is no longer a 409 path.
    session = await resolve_session_for_gm(db, session_id=session_id, auth=auth)
    if session is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    await logic.delete_session(db, session=session, redis_client=redis_client)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    session = await resolve_session_for_gm(db, session_id=session_id, auth=auth)
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
        pj = await logic.create_pj(
            db,
            name=payload.name,
            gm_key_id=auth.id,
            campaign_id=payload.campaign_id,
            user_id=payload.user_id,
            requester_user_id=auth.user_id,
        )
    except DuplicatePjError as exc:
        raise DuplicatePjConflictError(detail=str(exc)) from exc
    except PjCampaignResolutionError as exc:
        raise PjCampaignRequiredError(detail=str(exc)) from exc
    except (PjForbiddenError, CampaignAccessError) as exc:
        raise PjForbiddenAppError(detail=str(exc)) from exc
    except PjAssignmentError as exc:
        raise InvalidUserAssignmentError(detail=str(exc)) from exc
    return PjOut.model_validate(pj)


@router.get(
    "/pjs",
    response_model=Page[PjOut],
    summary="List the current MJ's PJs.",
)
async def list_pjs(
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    campaign_id: Annotated[UUID | None, Query()] = None,
) -> Page[PjOut]:
    try:
        rows = await logic.list_pjs(
            db,
            gm_key_id=auth.id,
            campaign_id=campaign_id,
            requester_user_id=auth.user_id,
        )
    except CampaignAccessError as exc:
        raise PjForbiddenAppError(detail=str(exc)) from exc
    items = [PjOut.model_validate(r) for r in rows]
    return Page[PjOut](items=items, total=len(items), page=1, size=len(items) or 1)


# ---------------------------------------------------------------------------
# Speaker ↔ PJ mapping (US3 — sub-lot 5a)
# ---------------------------------------------------------------------------


@router.patch(
    "/pjs/{pj_id}",
    response_model=PjOut,
    summary="Partially update a PJ owned by the current MJ.",
)
async def update_pj(
    pj_id: UUID,
    payload: PjUpdate,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> PjOut:
    try:
        pj = await logic.update_pj(
            db,
            pj_id=pj_id,
            gm_key_id=auth.id,
            name=payload.name,
            user_id=payload.user_id,
            update_name="name" in payload.model_fields_set,
            update_user_id="user_id" in payload.model_fields_set,
            requester_user_id=auth.user_id,
        )
    except DuplicatePjError as exc:
        raise DuplicatePjConflictError(detail=str(exc)) from exc
    except PjAssignmentError as exc:
        raise InvalidUserAssignmentError(detail=str(exc)) from exc
    except (PjForbiddenError, CampaignAccessError) as exc:
        raise PjNotFoundError(detail=str(exc)) from exc
    if pj is None:
        raise PjNotFoundError(detail=f"PJ {pj_id} not found.")
    return PjOut.model_validate(pj)


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
    - 409 wrong-mode if the session is non_diarised (use /players instead).
    - 409 if the session is not yet in ``state=transcribed``.
    - 422 if any ``pj_id`` is unknown or owned by another MJ.

    Side-effect: any existing ``pov:*`` artefact for this session is
    deleted (data-model.md §6 invariant). A subsequent
    ``POST /artifacts/povs`` is required to regenerate them.
    """
    session = await resolve_session_for_gm(db, session_id=session_id, auth=auth)
    if session is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    if session.transcription_mode is TranscriptionMode.NON_DIARISED:
        raise WrongModeError(
            detail=(
                f"Session {session_id} is in mode 'non_diarised'. "
                "Use POST /players to declare PJs for this session."
            ),
        )
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
            campaign_id=session.campaign_id,
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
    yet (resource exists but is not configured).

    409 wrong-mode if the session is non_diarised (use GET /players).
    """
    session = await resolve_session_for_gm(db, session_id=session_id, auth=auth)
    if session is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    if session.transcription_mode is TranscriptionMode.NON_DIARISED:
        raise WrongModeError(
            detail=(
                f"Session {session_id} is in mode 'non_diarised'. "
                "Use GET /players to read the players list."
            ),
        )
    result = await logic.get_session_mapping(db, session_id=session.id)
    return MappingOut(
        session_id=session.id,
        mapping=result.mapping,
        updated_at=result.updated_at,
    )


# ---------------------------------------------------------------------------
# Session players (feature 002 — non_diarised analog of /mapping)
# ---------------------------------------------------------------------------


@router.post(
    "/sessions/{session_id}/players",
    response_model=SessionPlayersOut,
    summary="Replace the list of PJ present at a non_diarised session.",
)
async def post_session_players(
    session_id: UUID,
    payload: SessionPlayersIn,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SessionPlayersOut:
    """Declare the PJ present at a non_diarised session (FR-012).

    PUT-like semantics — the provided list replaces the previous one
    integrally. Each ``pj_id`` must belong to the current MJ (422
    ``invalid-player-list`` otherwise). Reserved for non_diarised
    sessions (409 ``wrong-mode`` on diarised).
    """
    session = await resolve_session_for_gm(db, session_id=session_id, auth=auth)
    if session is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    if session.transcription_mode is not TranscriptionMode.NON_DIARISED:
        raise WrongModeError(
            detail=(
                f"Session {session_id} is in mode 'diarised'. "
                "Use PUT /mapping for this session."
            ),
        )

    try:
        pj_ids = await logic.set_session_players(
            db,
            session=session,
            pj_ids=payload.pj_ids,
            gm_key_id=auth.id,
            campaign_id=session.campaign_id,
        )
    except LogicInvalidPlayerListError as exc:
        raise InvalidPlayerListError(detail=str(exc)) from exc

    return SessionPlayersOut(
        session_id=session.id,
        pj_ids=pj_ids,
        updated_at=datetime.now(UTC),
    )


@router.get(
    "/sessions/{session_id}/players",
    response_model=SessionPlayersOut,
    summary="Read the current PJ list of a non_diarised session.",
)
async def get_session_players(
    session_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SessionPlayersOut:
    session = await resolve_session_for_gm(db, session_id=session_id, auth=auth)
    if session is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    if session.transcription_mode is not TranscriptionMode.NON_DIARISED:
        raise WrongModeError(
            detail=(
                f"Session {session_id} is in mode 'diarised'. "
                "Use GET /mapping for this session."
            ),
        )
    pj_ids = await logic.list_session_players(db, session=session)
    return SessionPlayersOut(
        session_id=session.id,
        pj_ids=pj_ids,
        updated_at=None,
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
    session = await resolve_session_for_gm(db, session_id=session_id, auth=auth)
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


@router.put(
    "/sessions/{session_id}/transcription",
    response_model=TranscriptionEditOut,
    summary="Persist the edited Markdown transcription for a session.",
)
async def put_transcription_edit(
    session_id: UUID,
    payload: TranscriptionEditIn,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> TranscriptionEditOut:
    session = await resolve_session_for_gm(db, session_id=session_id, auth=auth)
    if session is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    try:
        updated = await logic.save_session_transcription_edit(
            db, session=session, content_md=payload.content_md
        )
    except logic.SessionNotTranscribedForEditError as exc:
        raise TranscriptionEditNotTranscribedError(detail=str(exc)) from exc
    return TranscriptionEditOut(
        session_id=updated.id,
        content_md=updated.edited_transcript_md or "",
        is_edited=True,
        updated_at=updated.updated_at,
    )


@router.post(
    "/sessions/{session_id}/transcription/recover",
    response_model=SessionOut,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Session not found or not visible to the current GM."
        },
        status.HTTP_409_CONFLICT: {
            "description": (
                "Session is not stuck in 'transcribing', or its transcription "
                "job is still running."
            )
        },
    },
    summary="Recover a session wedged in 'transcribing' after a lost worker.",
)
async def recover_stuck_transcription(
    session_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    redis_client: Annotated[Redis, Depends(get_redis)],
) -> SessionOut:
    """Force the failed transition a crashed transcription worker never reached.

    When a worker dies mid-run the session can stay ``transcribing`` forever
    while its RQ job is gone from Redis. This GM-only action verifies the job
    is truly no longer active and moves the session to ``transcription_failed``
    so the audio can be replaced (or the session deleted). Refused with 409
    when the session is not in ``transcribing`` (``transcription-not-stuck``)
    or the job is still running (``transcription-still-active``).
    """
    session = await resolve_session_for_gm(db, session_id=session_id, auth=auth)
    if session is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    try:
        updated = await logic.recover_stuck_transcription(
            db, session=session, redis_client=redis_client
        )
    except logic.TranscriptionNotStuckError as exc:
        raise TranscriptionNotStuckError(detail=str(exc)) from exc
    except logic.TranscriptionStillActiveError as exc:
        raise TranscriptionStillActiveError(detail=str(exc)) from exc
    return SessionOut.model_validate(updated)


@router.post(
    "/sessions/{session_id}/transcription/restart",
    response_model=SessionOut,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Session not found or not visible to the current GM."
        },
        status.HTTP_409_CONFLICT: {
            "description": (
                "Session is not in a restartable state, or has no audio to "
                "re-transcribe."
            )
        },
    },
    summary="Re-run transcription on the session's existing audio.",
)
async def restart_transcription(
    session_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    redis_client: Annotated[Redis, Depends(get_redis)],
) -> SessionOut:
    """Re-enqueue transcription from the stored audio without a re-upload.

    Story 7.1 / BD-21: after a transcription failure (or to redo a finished
    transcription) the GM restarts here. The session returns to
    ``audio_uploaded`` with a fresh ``current_job_id`` and flows back through the
    normal pipeline. Refused (409) when the session is not in
    ``transcription_failed`` / ``transcribed`` (``transcription-restart-not-allowed``)
    or has no usable audio (``no-audio-to-transcribe``).
    """
    session = await resolve_session_for_gm(db, session_id=session_id, auth=auth)
    if session is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    try:
        updated = await logic.restart_transcription_for_session(
            db, session=session, redis_client=redis_client
        )
    except logic.TranscriptionRestartNotAllowedError as exc:
        raise TranscriptionRestartNotAllowedError(detail=str(exc)) from exc
    except logic.NoAudioToTranscribeError as exc:
        raise NoAudioToTranscribeError(detail=str(exc)) from exc
    return SessionOut.model_validate(updated)


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
    session = await resolve_session_for_gm(db, session_id=session_id, auth=auth)
    if session is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    if session.edited_transcript_md is not None:
        if session.state != SessionState.TRANSCRIBED:
            raise TranscriptionNotReadyError(
                detail=(
                    f"Transcription for session {session_id} is not available yet."
                ),
            )
        return Response(
            content=session.edited_transcript_md,
            media_type="text/markdown; charset=utf-8",
        )
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
    force: Annotated[
        bool,
        Query(description="Confirm overwrite of a manually edited artifact."),
    ] = False,
) -> JobQueuedOut:
    session_row = await resolve_session_for_gm(
        db, session_id=session_id, auth=auth
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
    if session_row.transcription_mode is TranscriptionMode.NON_DIARISED:
        summary_row = await ArtifactRepository(db).get(session_id, "summary")
        if summary_row is None and not _has_edited_transcription(session_row):
            raise NoSummaryError(
                detail=(
                    f"Session {session_id} has no global summary yet. "
                    "POST /artifacts/summary first (FR-010)."
                ),
            )

    await _guard_regeneration_not_edited(
        db,
        session_id=session_id,
        force=force,
        target_kinds=("narrative",),
    )
    job_id = await logic.enqueue_session_job(
        db,
        session=session_row,
        redis_client=redis_client,
        kind=JobKind.NARRATIVE,
        job_func=generate_narrative_job,
    )
    return JobQueuedOut(
        id=job_id,
        kind=JobKind.NARRATIVE,
        session_id=session_id,
        status=JobStatus.QUEUED,
        queued_at=datetime.now(UTC),
    )


def _artifact_provenance(artifact) -> dict:
    """Provenance kwargs shared by every ``*ArtifactOut`` projection (BD-24).

    Surfaces whether an artefact was hand-edited since its last AI generation.
    """
    return {
        "is_edited": artifact.is_edited,
        "edited_at": artifact.edited_at,
        "edited_by": artifact.edited_by,
    }


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
    session_row = await resolve_session_for_gm(
        db, session_id=session_id, auth=auth
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
        **_artifact_provenance(artifact),
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
    session = await resolve_session_for_gm(db, session_id=session_id, auth=auth)
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
    force: Annotated[
        bool,
        Query(description="Confirm overwrite of a manually edited artifact."),
    ] = False,
) -> JobQueuedOut:
    session_row = await resolve_session_for_gm(
        db, session_id=session_id, auth=auth
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
    if session_row.transcription_mode is TranscriptionMode.NON_DIARISED:
        summary_row = await ArtifactRepository(db).get(session_id, "summary")
        if summary_row is None and not _has_edited_transcription(session_row):
            raise NoSummaryError(
                detail=(
                    f"Session {session_id} has no global summary yet. "
                    "POST /artifacts/summary first (FR-010)."
                ),
            )

    await _guard_regeneration_not_edited(
        db,
        session_id=session_id,
        force=force,
        target_kinds=("elements",),
    )
    job_id = await logic.enqueue_session_job(
        db,
        session=session_row,
        redis_client=redis_client,
        kind=JobKind.ELEMENTS,
        job_func=generate_elements_job,
    )
    return JobQueuedOut(
        id=job_id,
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
    session_row = await resolve_session_for_gm(
        db, session_id=session_id, auth=auth
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

    return ElementsArtifactOut(
        session_id=artifact.session_id,
        elements=[Element(**row) for row in elements_from_content(artifact.content_json)],
        model_used=artifact.model_used,
        generated_at=artifact.generated_at,
        **_artifact_provenance(artifact),
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
    session = await resolve_session_for_gm(db, session_id=session_id, auth=auth)
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
# Summary artefact (feature 002 — non_diarised map-reduce)
# ---------------------------------------------------------------------------


@router.post(
    "/sessions/{session_id}/artifacts/summary",
    response_model=JobQueuedOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger the global session summary (map-reduce LLM).",
)
async def post_summary(
    session_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    redis_client: Annotated[Redis, Depends(get_redis)],
    force: Annotated[
        bool,
        Query(description="Confirm overwrite of manually edited artifacts."),
    ] = False,
) -> JobQueuedOut:
    """Enqueue the map-reduce summary job for a non_diarised session.

    Pre-conditions:
    - session must belong to the MJ (404 otherwise)
    - session must be in non_diarised mode (409 wrong-mode)
    - session must be in state=transcribed (409 session-not-transcribed)
    - session must have at least 1 chunk, unless it has an edited Markdown
      transcription override (409 no-chunks)

    Side effect documented under FR-011: each new summary job resets
    chunks.summary_text and cascade-deletes existing narrative /
    elements / pov:* artefacts for the session. The atomicity is
    enforced by ``_generate_summary`` (see research.md §2).
    """
    session_row = await resolve_session_for_gm(
        db, session_id=session_id, auth=auth
    )
    if session_row is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    if session_row.transcription_mode is not TranscriptionMode.NON_DIARISED:
        raise WrongModeError(
            detail=(
                f"Session {session_id} is in mode 'diarised'. "
                "Map-reduce summary is reserved for non_diarised sessions."
            ),
        )
    if session_row.state != SessionState.TRANSCRIBED:
        raise SessionNotTranscribedError(
            detail=(
                f"Session {session_id} is in state {session_row.state.value!r}; "
                "summary generation requires 'transcribed'."
            ),
        )
    chunks = await logic.list_session_chunks(db, session=session_row)
    if not chunks and not _has_edited_transcription(session_row):
        raise NoChunksError(
            detail=(
                f"Session {session_id} has no chunks; transcription job "
                "may not have produced output yet."
            ),
        )

    await _guard_regeneration_not_edited(
        db,
        session_id=session_id,
        force=force,
        target_kinds=("summary",),
        include_downstream=True,
    )
    job_id = await logic.enqueue_session_job(
        db,
        session=session_row,
        redis_client=redis_client,
        kind=JobKind.SUMMARY,
        job_func=generate_summary_job,
    )
    return JobQueuedOut(
        id=job_id,
        kind=JobKind.SUMMARY,
        session_id=session_id,
        status=JobStatus.QUEUED,
        queued_at=datetime.now(UTC),
    )


@router.get(
    "/sessions/{session_id}/artifacts/summary",
    response_model=SummaryArtifactOut,
    summary="Fetch the global session summary (JSON).",
)
async def get_summary(
    session_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SummaryArtifactOut:
    session_row = await resolve_session_for_gm(
        db, session_id=session_id, auth=auth
    )
    if session_row is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    if session_row.transcription_mode is not TranscriptionMode.NON_DIARISED:
        raise WrongModeError(
            detail=(
                f"Session {session_id} is in mode 'diarised'. "
                "Summary artefact is reserved for non_diarised sessions."
            ),
        )
    artifact = await ArtifactRepository(db).get(session_id, "summary")
    if artifact is None:
        raise ArtifactNotReadyError(
            detail=(
                f"Summary for session {session_id} has not been generated yet. "
                "POST to this endpoint to enqueue the job."
            ),
        )
    content = artifact.content_json or {}
    text = str(content.get("text", "")) if isinstance(content, dict) else ""
    return SummaryArtifactOut(
        session_id=artifact.session_id,
        text=text,
        model_used=artifact.model_used,
        generated_at=artifact.generated_at,
        **_artifact_provenance(artifact),
    )


@router.get(
    "/sessions/{session_id}/artifacts/summary.md",
    response_class=Response,
    summary="Export the global session summary as Markdown (text/markdown).",
)
async def get_summary_md(
    session_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    session_row = await resolve_session_for_gm(
        db, session_id=session_id, auth=auth
    )
    if session_row is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    if session_row.transcription_mode is not TranscriptionMode.NON_DIARISED:
        raise WrongModeError(
            detail=(
                f"Session {session_id} is in mode 'diarised'. "
                "Summary artefact is reserved for non_diarised sessions."
            ),
        )
    artifact = await ArtifactRepository(db).get(session_id, "summary")
    if artifact is None:
        raise ArtifactNotReadyError(
            detail=(
                f"Summary for session {session_id} has not been generated yet."
            ),
        )
    md = render_summary_md(session_row, artifact)
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
    force: Annotated[
        bool,
        Query(description="Confirm overwrite of manually edited POV artifacts."),
    ] = False,
) -> JobQueuedOut:
    """Enqueue a single job that generates one ``pov:<pj_id>`` artefact
    per row in the session's mapping. Pre-conditions match the other
    generators (404 if foreign, 409 if not transcribed) plus FR-011:
    a 409 ``no-mapping`` is raised when the session has no configured
    speaker-PJ mapping yet — the operator must call
    ``PUT /mapping`` first.
    """
    session_row = await resolve_session_for_gm(
        db, session_id=session_id, auth=auth
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
    if session_row.transcription_mode is TranscriptionMode.NON_DIARISED:
        # Non-diarised: require either a global summary or an edited transcription,
        # plus a non-empty /players list before generating POV artifacts.
        summary_row = await ArtifactRepository(db).get(session_id, "summary")
        if summary_row is None and not _has_edited_transcription(session_row):
            raise NoSummaryError(
                detail=(
                    f"Session {session_id} has no global summary yet. "
                    "POST /artifacts/summary first (FR-010)."
                ),
            )
        players = await logic.list_session_players(db, session=session_row)
        if not players:
            raise NoMappingError(
                detail=(
                    f"Session {session_id} has no PJ declared via POST /players. "
                    "Declare at least one PJ before generating POVs."
                ),
            )
    else:
        # Diarised : Jalon 5 path inchangé.
        mappings = await MappingRepository(db).get_for_session(session_id)
        if not mappings:
            raise NoMappingError(
                detail=(
                    f"Session {session_id} has no speaker-PJ mapping configured. "
                    "Call PUT /services/jdr/sessions/{id}/mapping first."
                ),
            )

    await _guard_regeneration_not_edited(
        db,
        session_id=session_id,
        force=force,
        include_povs=True,
    )
    job_id = await logic.enqueue_session_job(
        db,
        session=session_row,
        redis_client=redis_client,
        kind=JobKind.POVS,
        job_func=generate_povs_job,
    )
    return JobQueuedOut(
        id=job_id,
        kind=JobKind.POVS,
        session_id=session_id,
        status=JobStatus.QUEUED,
        queued_at=datetime.now(UTC),
    )


async def _load_owned_pj_or_404(
    db: AsyncSession,
    *,
    pj_id: UUID,
    gm_key_id: UUID,
    campaign_id: UUID | None = None,
):
    """Return the PJ row or raise ``PjNotFoundError`` (404).

    Both "doesn't exist" and "owned by another MJ" collapse to 404 so a
    MJ cannot probe the existence of foreign PJ ids.
    """
    pj = await PjRepository(db).find_by_id_owned_by(
        pj_id, gm_key_id, campaign_id
    )
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

    session_row = await resolve_session_for_gm(
        db, session_id=session_id, auth=auth
    )
    if session_row is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    pj = await _load_owned_pj_or_404(
        db, pj_id=pj_id, gm_key_id=auth.id, campaign_id=session_row.campaign_id
    )

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
        **_artifact_provenance(artifact),
    )


# ---------------------------------------------------------------------------
# Artifact editing — synchronous MJ edits (BD-23 / Epic 8 Story 8.1)
# ---------------------------------------------------------------------------


@router.patch(
    "/sessions/{session_id}/artifacts/summary",
    response_model=SummaryArtifactOut,
    summary="Edit the global session summary (synchronous write, MJ only).",
)
async def patch_summary(
    session_id: UUID,
    payload: TextEditIn,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SummaryArtifactOut:
    """Replace the summary text in one synchronous write (BD-23).

    Ownership enforced by ``resolve_session_for_gm`` (404 if the session is not
    the caller's). The artefact must already exist (404 ``artifact-not-ready``
    otherwise — same semantics as GET). Sets ``is_edited``/``edited_at`` and
    leaves ``model_used``/``generated_at`` untouched (FR-006).
    """
    session_row = await resolve_session_for_gm(db, session_id=session_id, auth=auth)
    if session_row is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    artifact = await ArtifactRepository(db).update_content(
        session_id,
        kind="summary",
        content_json={"text": payload.text},
        edited_by=str(auth.id),
    )
    if artifact is None:
        raise ArtifactNotReadyError(
            detail=(
                f"Summary for session {session_id} has not been generated yet; "
                "generate it before editing."
            ),
        )
    return SummaryArtifactOut(
        session_id=artifact.session_id,
        text=str(artifact.content_json.get("text", "")),
        model_used=artifact.model_used,
        generated_at=artifact.generated_at,
        **_artifact_provenance(artifact),
    )


@router.patch(
    "/sessions/{session_id}/artifacts/narrative",
    response_model=NarrativeArtifactOut,
    summary="Edit the narrative summary (synchronous write, MJ only).",
)
async def patch_narrative(
    session_id: UUID,
    payload: TextEditIn,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> NarrativeArtifactOut:
    """Replace the narrative text in one synchronous write (BD-23). Same
    ownership / artefact-absent / provenance semantics as ``patch_summary``."""
    session_row = await resolve_session_for_gm(db, session_id=session_id, auth=auth)
    if session_row is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    artifact = await ArtifactRepository(db).update_content(
        session_id,
        kind="narrative",
        content_json={"text": payload.text},
        edited_by=str(auth.id),
    )
    if artifact is None:
        raise ArtifactNotReadyError(
            detail=(
                f"Narrative for session {session_id} has not been generated yet; "
                "generate it before editing."
            ),
        )
    return NarrativeArtifactOut(
        session_id=artifact.session_id,
        text=str(artifact.content_json.get("text", "")),
        model_used=artifact.model_used,
        generated_at=artifact.generated_at,
        **_artifact_provenance(artifact),
    )


@router.patch(
    "/sessions/{session_id}/artifacts/povs/{pj_id}",
    response_model=PovArtifactOut,
    summary="Edit one PJ's POV (synchronous write, MJ only).",
)
async def patch_pov(
    session_id: UUID,
    pj_id: UUID,
    payload: TextEditIn,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> PovArtifactOut:
    """Replace one PJ's POV text in one synchronous write (BD-23).

    Resolves session ownership then the owned PJ (404 on either mismatch),
    before editing the ``pov:<pj_id>`` artefact.
    """
    session_row = await resolve_session_for_gm(db, session_id=session_id, auth=auth)
    if session_row is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    await _load_owned_pj_or_404(
        db, pj_id=pj_id, gm_key_id=auth.id, campaign_id=session_row.campaign_id
    )
    artifact = await ArtifactRepository(db).update_content(
        session_id,
        kind=f"pov:{pj_id}",
        content_json={"text": payload.text},
        edited_by=str(auth.id),
    )
    if artifact is None:
        raise ArtifactNotReadyError(
            detail=(
                f"POV for PJ {pj_id} in session {session_id} has not been "
                "generated yet; generate it before editing."
            ),
        )
    return PovArtifactOut(
        session_id=artifact.session_id,
        pj_id=pj_id,
        text=str(artifact.content_json.get("text", "")),
        model_used=artifact.model_used,
        generated_at=artifact.generated_at,
        **_artifact_provenance(artifact),
    )


@router.put(
    "/sessions/{session_id}/artifacts/elements",
    response_model=ElementsArtifactOut,
    summary="Replace the elements card (synchronous full write, MJ only).",
)
async def put_elements(
    session_id: UUID,
    payload: ElementsPutIn,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ElementsArtifactOut:
    """Atomically replace the whole elements card (BD-23/BD-26).

    Full-replace (PUT) rather than per-element CRUD: atomic, idempotent, and it
    mirrors the existing ``PUT /mapping`` pattern. Same ownership /
    artefact-absent / provenance semantics as the text edits.
    """
    session_row = await resolve_session_for_gm(db, session_id=session_id, auth=auth)
    if session_row is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")
    content_json = {"elements": [e.model_dump() for e in payload.elements]}
    artifact = await ArtifactRepository(db).update_content(
        session_id,
        kind="elements",
        content_json=content_json,
        edited_by=str(auth.id),
    )
    if artifact is None:
        raise ArtifactNotReadyError(
            detail=(
                f"Elements for session {session_id} have not been generated yet; "
                "generate them before editing."
            ),
        )
    return ElementsArtifactOut(
        session_id=artifact.session_id,
        elements=[Element(**row) for row in elements_from_content(artifact.content_json)],
        model_used=artifact.model_used,
        generated_at=artifact.generated_at,
        **_artifact_provenance(artifact),
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
        campaign_id = await _campaign_id_for_auth(db, auth)
        result = await logic.enroll_player(
            db,
            name=payload.name,
            pj_id=payload.pj_id,
            gm_key_id=auth.id,
            campaign_id=campaign_id,
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
    campaign_id = await _campaign_id_for_auth(db, auth)
    revoked = await logic.revoke_player(
        db, player_id=player_id, gm_key_id=auth.id, campaign_id=campaign_id
    )
    if not revoked:
        raise PlayerNotFoundError(detail=f"Player {player_id} not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Player read endpoints (/me/* — require_player + FR-014 isolation)
# ---------------------------------------------------------------------------


async def _ensure_player_can_read_session(
    db: AsyncSession,
    *,
    session_id: UUID,
    player_pj_id: UUID | None,
    campaign_id: UUID | None = None,
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
        db,
        session_id=session_id,
        pj_id=player_pj_id,
        campaign_id=campaign_id,
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
    campaign_id = await _campaign_id_for_auth(db, auth)
    pj = await logic.get_player_pj(
        db, pj_id=auth.pj_id, campaign_id=campaign_id
    )
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
    campaign_id = await _campaign_id_for_auth(db, auth)
    sessions = await logic.list_player_sessions(
        db, player_pj_id=auth.pj_id, campaign_id=campaign_id
    )
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
    campaign_id = await _campaign_id_for_auth(db, auth)
    await _ensure_player_can_read_session(
        db,
        session_id=session_id,
        player_pj_id=auth.pj_id,
        campaign_id=campaign_id,
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
    campaign_id = await _campaign_id_for_auth(db, auth)
    await _ensure_player_can_read_session(
        db,
        session_id=session_id,
        player_pj_id=auth.pj_id,
        campaign_id=campaign_id,
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
    campaign_id = await _campaign_id_for_auth(db, auth)
    await _ensure_player_can_read_session(
        db,
        session_id=session_id,
        player_pj_id=auth.pj_id,
        campaign_id=campaign_id,
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
    campaign_id = await _campaign_id_for_auth(db, auth)
    await _ensure_player_can_read_session(
        db,
        session_id=session_id,
        player_pj_id=auth.pj_id,
        campaign_id=campaign_id,
    )
    session = await db.scalar(
        select(SessionModel).where(SessionModel.id == session_id)
    )
    pj = await logic.get_player_pj(
        db, pj_id=auth.pj_id, campaign_id=campaign_id
    )
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
    return await _project_job_out(
        job_id=job_id, auth=auth, db=db, redis_client=redis_client
    )


@router.get(
    "/jobs/{job_id}/events",
    response_class=StreamingResponse,
    responses={
        status.HTTP_200_OK: {
            "description": (
                "Server-Sent Events stream. Each frame uses `event: progress` "
                "and a JSON `data` payload with status, phase, progress_percent, "
                "and failure_reason when a failure reason is available."
            ),
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                    "example": (
                        "event: progress\n"
                        'data: {"status":"running","phase":null,'
                        '"progress_percent":null}\n\n'
                    ),
                }
            },
        }
    },
    summary="Stream live status events for a JDR-service async job.",
)
async def get_job_events(
    job_id: str,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    redis_client: Annotated[Redis, Depends(get_redis)],
) -> StreamingResponse:
    """Stream the same public job status projection used by GET /jobs/{job_id}.

    We validate visibility before opening the stream so unknown or foreign jobs
    keep the existing HTTP 404 behavior instead of becoming in-stream failures.
    """
    initial_job_out = await _project_job_out(
        job_id=job_id, auth=auth, db=db, redis_client=redis_client
    )
    # Visibility is validated; the stream loop below reads only Redis. Release the
    # pooled DB connection NOW instead of holding it (idle-in-transaction) for the
    # whole job. A StreamingResponse keeps request-scoped `Depends` alive until the
    # body finishes, so without this close every open SSE pins one of the ~15 pool
    # connections for minutes and saturates the pool (see investigation
    # db-paralysis-long-jobs). `get_db_session` teardown still runs after the
    # stream; committing a closed session is a no-op for these read-only queries.
    await db.close()
    return StreamingResponse(
        _job_event_stream(
            initial_job_out=initial_job_out,
            job_id=job_id,
            redis_client=redis_client,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
