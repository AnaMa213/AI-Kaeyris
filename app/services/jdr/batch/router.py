"""Batch-mode sub-router for the JDR service.

Hosts every route that handles uploaded artefacts (vs. the live mode in
``../live/router.py``). Mounted under the main JDR router so it inherits
the default auth + rate-limit dependencies.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Response, UploadFile, status
from redis import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedKey, require_gm
from app.core.db import get_db_session
from app.core.errors import AppError
from app.core.redis_client import get_redis
from app.services.jdr import logic
from app.services.jdr.campaigns import resolve_campaign_for_auth
from app.services.jdr.logic import (
    AudioAlreadyUploadedError,
    AudioPurgeBlockedError,
    NoAudioToPurgeError,
    UnsupportedAudioMimeError,
)
from app.services.jdr.schemas import AudioUploadOut


class SessionNotFoundError(AppError):
    """Session does not exist or does not belong to the current MJ."""

    status_code = status.HTTP_404_NOT_FOUND
    error_type = "session-not-found"
    title = "Session not found"


class UnsupportedAudioMime(AppError):
    """The uploaded file is not an M4A."""

    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    error_type = "unsupported-audio-mime"
    title = "Unsupported audio MIME type"


class AudioAlreadyUploaded(AppError):
    """The session already has an audio source — re-uploading is refused."""

    status_code = status.HTTP_409_CONFLICT
    error_type = "audio-already-uploaded"
    title = "Audio already uploaded"


class AudioPurgeConflict(AppError):
    """The session is in a state where the audio cannot be safely purged.

    Triggers: ``state in {transcribing, transcribed}``. Transcribing because
    the worker has the file open; transcribed because the file was already
    purged by the auto-cleanup that follows a successful transcription.
    """

    status_code = status.HTTP_409_CONFLICT
    error_type = "audio-purge-conflict"
    title = "Audio purge conflict"


class AudioNotFound(AppError):
    """No audio source attached to this session — nothing to delete."""

    status_code = status.HTTP_404_NOT_FOUND
    error_type = "audio-not-found"
    title = "Audio not found"


router = APIRouter()


async def _active_campaign_id(
    db: AsyncSession,
    auth: AuthenticatedKey,
) -> UUID | None:
    context = await resolve_campaign_for_auth(db, auth)
    return context.id if context is not None else None


@router.post(
    "/sessions/{session_id}/audio",
    response_model=AudioUploadOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload the audio of a session and enqueue its transcription.",
)
async def post_audio(
    session_id: UUID,
    audio: Annotated[
        UploadFile,
        File(
            description=(
                "Audio file (M4A). The upload itself is streamed to disk and "
                "has no hard size limit at this layer. Note: with "
                "TRANSCRIPTION_PROVIDER=cloud, the OpenAI Whisper API caps "
                "individual requests at 25 MB, so the transcription job has to "
                "chunk larger files (R3 — to be implemented). With provider=local "
                "(LAN GPU host running faster-whisper), any size is supported."
            ),
        ),
    ],
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    redis_client: Annotated[Redis, Depends(get_redis)],
) -> AudioUploadOut:
    campaign_id = await _active_campaign_id(db, auth)
    session = await logic.get_session(
        db, session_id=session_id, gm_key_id=auth.id, campaign_id=campaign_id
    )
    if session is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")

    try:
        result = await logic.store_audio_source_for_session(
            db,
            session=session,
            upload_file=audio,
            redis_client=redis_client,
        )
    except UnsupportedAudioMimeError as exc:
        raise UnsupportedAudioMime(detail=str(exc)) from exc
    except AudioAlreadyUploadedError as exc:
        raise AudioAlreadyUploaded(detail=str(exc)) from exc

    return AudioUploadOut(
        session_id=result.audio_source.session_id,
        path=result.audio_source.path,
        sha256=result.audio_source.sha256,
        size_bytes=result.audio_source.size_bytes,
        duration_seconds=result.audio_source.duration_seconds,
        uploaded_at=result.audio_source.uploaded_at,
        job_id=result.job_id,
    )


@router.delete(
    "/sessions/{session_id}/audio",
    status_code=status.HTTP_204_NO_CONTENT,
    summary=(
        "Purge a session's audio file and reset the session so a new upload "
        "can be sent."
    ),
)
async def delete_audio(
    session_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    """Drop the audio file on disk and reset the session to ``created``.

    Use cases (matches data-model.md §3 retry arrow
    ``transcription_failed → audio_uploaded`` and the analogous
    cancel-before-transcription path):

    - The MJ uploaded the wrong file and wants to re-upload before the
      worker picks it up.
    - Transcription failed and the MJ wants to try again with a different
      audio (or after the upstream provider has recovered).

    Refused (409) when ``state ∈ {transcribing, transcribed}``: see
    ``logic.purge_audio_for_session`` for the rationale.
    """
    campaign_id = await _active_campaign_id(db, auth)
    session = await logic.get_session(
        db, session_id=session_id, gm_key_id=auth.id, campaign_id=campaign_id
    )
    if session is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")

    try:
        await logic.purge_audio_for_session(db, session=session)
    except AudioPurgeBlockedError as exc:
        raise AudioPurgeConflict(detail=str(exc)) from exc
    except NoAudioToPurgeError as exc:
        raise AudioNotFound(detail=str(exc)) from exc

    return Response(status_code=status.HTTP_204_NO_CONTENT)
