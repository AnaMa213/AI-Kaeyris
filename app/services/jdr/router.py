"""Top-level router for the JDR service.

Mounted at ``/services/jdr`` (CLAUDE.md §4.2). Every route attached
here inherits the default dependencies declared on the router:

- ``require_api_key`` enforces a valid Bearer token (jalon 2).
- ``enforce_rate_limit`` applies a per-key sliding window (jalon 3).

Role-based authorisation is expressed at the route level via
``Depends(require_gm)`` / ``Depends(require_player)``. Both extend
``require_api_key`` (FastAPI caches the dependency per request).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
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
from app.services.jdr import logic
from app.services.jdr.batch.router import router as batch_router
from app.services.jdr.db.repositories import TranscriptionRepository
from app.services.jdr.schemas import (
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
