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

from fastapi import APIRouter, Depends, Response, status
from redis import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    AuthenticatedKey,
    UnauthorizedError,
    require_api_key,
    require_gm,
)
from app.core.db import get_db_session
from app.core.errors import AppError
from app.core.rate_limit import enforce_rate_limit
from app.core.redis_client import get_redis
from app.jobs import enqueue_job, get_default_queue
from app.jobs.jdr import generate_narrative_job
from rq.exceptions import NoSuchJobError
from rq.job import Job
from app.services.jdr import logic
from app.services.jdr.batch.router import router as batch_router
from app.services.jdr.db.models import JobKind, JobStatus, SessionState
from app.services.jdr.db.repositories import (
    ArtifactRepository,
    TranscriptionRepository,
)
from app.services.jdr.markdown import (
    render_narrative_md,
    render_transcription_md,
)
from app.services.jdr.schemas import (
    JobOut,
    JobQueuedOut,
    NarrativeArtifactOut,
    Page,
    SessionCreate,
    SessionOut,
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


# Function name -> JobKind mapping. RQ pickles the callable by its module
# path; we use the inverse to expose a stable enum in JobOut.
_FUNC_NAME_TO_KIND: dict[str, JobKind] = {
    "app.jobs.jdr.transcribe_session_job": JobKind.TRANSCRIPTION,
    "app.jobs.jdr.generate_narrative_job": JobKind.NARRATIVE,
    # US2/US3 will register the remaining kinds when they land.
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
    """
    row = await logic.create_session(
        db,
        title=payload.title,
        recorded_at=payload.recorded_at,
        gm_key_id=auth.id,
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
