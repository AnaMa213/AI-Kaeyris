"""Batch-mode sub-router for the JDR service.

Hosts every route that handles uploaded artefacts (vs. the live mode in
``../live/router.py``). Mounted under the main JDR router so it inherits
the default auth + rate-limit dependencies.
"""

from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from redis import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedKey, require_gm
from app.core.db import get_db_session
from app.core.errors import AppError
from app.core.redis_client import get_redis
from app.services.jdr import logic
from app.services.jdr.campaign_context import resolve_campaign_scope_for_auth
from app.services.jdr.db.models import Session as SessionModel
from app.services.jdr.db.repositories import CampaignRepository
from app.services.jdr.logic import (
    AudioAlreadyUploadedError,
    AudioPurgeBlockedError,
    AudioReadNotFoundError,
    NoAudioToPurgeError,
    UnsupportedAudioMimeError,
)
from app.services.jdr.schemas import AudioUploadOut

_AUDIO_CACHE_CONTROL = "private, max-age=3600"
_AUDIO_STREAM_CHUNK_SIZE = 64 * 1024


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

    Triggers when ``state == transcribing`` because the worker may have the
    file open and be writing derived rows.
    """

    status_code = status.HTTP_409_CONFLICT
    error_type = "audio-purge-conflict"
    title = "Audio purge conflict"


class AudioNotFound(AppError):
    """No audio source attached to this session — nothing to delete."""

    status_code = status.HTTP_404_NOT_FOUND
    error_type = "audio-not-found"
    title = "Audio not found"


class AudioRangeNotSatisfiable(AppError):
    """The requested byte range cannot be served."""

    status_code = status.HTTP_416_RANGE_NOT_SATISFIABLE
    error_type = "audio-range-not-satisfiable"
    title = "Audio range not satisfiable"


router = APIRouter()


async def _campaign_id_for_auth(
    db: AsyncSession,
    auth: AuthenticatedKey,
) -> UUID | None:
    scope = await resolve_campaign_scope_for_auth(db, auth)
    return scope.campaign_id if scope is not None else None


async def _session_for_audio_read(
    db: AsyncSession,
    *,
    session_id: UUID,
    auth: AuthenticatedKey,
    campaign_id: UUID | None,
) -> SessionModel | None:
    session = await logic.get_session(
        db,
        session_id=session_id,
        gm_key_id=auth.id,
        campaign_id=campaign_id,
    )
    if session is not None:
        return session

    if auth.source != "web_session" or auth.user_id is None:
        return None

    row = await db.get(SessionModel, session_id)
    if row is None or row.campaign_id is None:
        return None
    if campaign_id is not None and row.campaign_id != campaign_id:
        return None

    membership = await CampaignRepository(db).get_membership(
        user_id=auth.user_id,
        campaign_id=row.campaign_id,
    )
    if membership is None:
        return None
    return row


def _audio_headers() -> dict[str, str]:
    return {
        "Accept-Ranges": "bytes",
        "Cache-Control": _AUDIO_CACHE_CONTROL,
    }


def _parse_range_header(range_header: str | None, size_bytes: int) -> tuple[int, int] | None:
    if range_header is None:
        return None
    value = range_header.strip()
    if not value:
        return None
    if size_bytes <= 0 or not value.startswith("bytes="):
        raise ValueError("Invalid byte range.")

    range_spec = value.removeprefix("bytes=").strip()
    if "," in range_spec or "-" not in range_spec:
        raise ValueError("Invalid byte range.")

    start_raw, end_raw = range_spec.split("-", 1)
    try:
        if start_raw == "":
            suffix_length = int(end_raw)
            if suffix_length <= 0:
                raise ValueError("Invalid byte range.")
            start = max(size_bytes - suffix_length, 0)
            end = size_bytes - 1
        else:
            start = int(start_raw)
            end = size_bytes - 1 if end_raw == "" else int(end_raw)
    except ValueError as exc:
        raise ValueError("Invalid byte range.") from exc

    if start < 0 or start >= size_bytes or end < start:
        raise ValueError("Invalid byte range.")
    return start, min(end, size_bytes - 1)


def _iter_file_range(path, *, start: int, length: int) -> Iterator[bytes]:
    remaining = length
    with path.open("rb") as file:
        file.seek(start)
        while remaining > 0:
            chunk = file.read(min(_AUDIO_STREAM_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


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
    campaign_id = await _campaign_id_for_auth(db, auth)
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


@router.get(
    "/sessions/{session_id}/audio",
    response_model=None,
    summary="Retrieve the source audio for a session.",
)
async def get_audio(
    session_id: UUID,
    request: Request,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    campaign_id = await _campaign_id_for_auth(db, auth)
    session = await _session_for_audio_read(
        db,
        session_id=session_id,
        auth=auth,
        campaign_id=campaign_id,
    )
    if session is None:
        raise SessionNotFoundError(detail=f"Session {session_id} not found.")

    try:
        audio = await logic.get_audio_for_session(db, session=session)
        range_result = _parse_range_header(
            request.headers.get("range"),
            audio.size_bytes,
        )
    except AudioReadNotFoundError as exc:
        raise AudioNotFound(detail=str(exc)) from exc
    except ValueError as exc:
        raise AudioRangeNotSatisfiable(
            detail=str(exc),
            headers={"Content-Range": f"bytes */{audio.size_bytes}"},
        ) from exc

    headers = _audio_headers()
    if range_result is None:
        return FileResponse(
            audio.path,
            media_type=audio.media_type,
            headers=headers,
        )

    start, end = range_result
    length = end - start + 1
    headers.update(
        {
            "Content-Range": f"bytes {start}-{end}/{audio.size_bytes}",
            "Content-Length": str(length),
        }
    )
    return StreamingResponse(
        _iter_file_range(audio.path, start=start, length=length),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=audio.media_type,
        headers=headers,
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

    Use cases:

    - The MJ uploaded the wrong file and wants to replace it.
    - The MJ wants to restart from a clean audio/transcription state.

    Refused (409) when ``state == transcribing``: see
    ``logic.purge_audio_for_session`` for the rationale.
    """
    campaign_id = await _campaign_id_for_auth(db, auth)
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
