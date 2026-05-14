"""Business logic for the JDR service.

ADR 0006 §5. Pure orchestrators that compose repositories and adapters.
The HTTP-specific ``UploadFile`` type is imported from starlette for
streaming uploads — its only HTTP semantics is the `.read(chunk_size)`
contract, so calling these helpers from a non-HTTP context (e.g. a CLI
ingestion script later) would only require adapting the input shape.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from redis import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.core.config import settings
from app.jobs import enqueue_job, get_default_queue
from app.jobs.jdr import transcribe_session_job
from app.services.jdr.db.models import AudioSource, Session, SessionState
from app.services.jdr.db.repositories import SessionRepository

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 64 * 1024  # 64 KiB streaming reads
ACCEPTED_AUDIO_MIMES: frozenset[str] = frozenset(
    {"audio/mp4", "audio/m4a", "audio/x-m4a"}
)


# ---------------------------------------------------------------------------
# Domain errors (logic-layer ; routes map them to HTTP)
# ---------------------------------------------------------------------------


class UnsupportedAudioMimeError(Exception):
    """The uploaded file is not an M4A."""


class AudioAlreadyUploadedError(Exception):
    """A session already has an attached audio source."""


class AudioPurgeBlockedError(Exception):
    """The session is in a state where the audio cannot be safely purged.

    Reasons (the route maps both to HTTP 409 with distinct detail
    messages):

    - ``state == transcribing``: a worker has the file open and is
      writing to the DB; purging now would race the job.
    - ``state == transcribed``: the audio was already auto-purged after a
      successful transcription, the call is a no-op from the caller's
      point of view.
    """


class NoAudioToPurgeError(Exception):
    """The session has no audio source on record — nothing to delete."""


# ---------------------------------------------------------------------------
# Sessions (CRUD already exposed at jalon 5 sub-lot 3a)
# ---------------------------------------------------------------------------


async def create_session(
    db: AsyncSession,
    *,
    title: str,
    recorded_at: datetime,
    gm_key_id: UUID,
    campaign_context: str | None = None,
) -> Session:
    return await SessionRepository(db).create(
        title=title,
        recorded_at=recorded_at,
        gm_key_id=gm_key_id,
        campaign_context=campaign_context,
    )


async def update_session(
    db: AsyncSession,
    *,
    session: Session,
    title: str | None = None,
    campaign_context: str | None = None,
    set_campaign_context: bool = False,
) -> Session:
    """Apply a partial update to a session.

    ``title``: when not ``None``, replaces the current title.
    ``campaign_context``: kept as a separate ``set_*`` flag because the
    spec allows clearing the field by sending an explicit ``null`` — and
    we can't tell "user sent null" from "user didn't send anything" with
    a single param. The route is responsible for translating the
    PATCH payload into ``set_campaign_context``.
    """
    if title is not None:
        session.title = title
    if set_campaign_context:
        session.campaign_context = campaign_context
    await db.commit()
    await db.refresh(session)
    return session


async def list_sessions(
    db: AsyncSession, *, gm_key_id: UUID
) -> list[Session]:
    return await SessionRepository(db).list_for_gm(gm_key_id)


async def get_session(
    db: AsyncSession, *, session_id: UUID, gm_key_id: UUID
) -> Session | None:
    return await SessionRepository(db).get_for_gm(session_id, gm_key_id)


# ---------------------------------------------------------------------------
# Audio upload (sub-lot 3b)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AudioUploadResult:
    """What ``store_audio_source_for_session`` returns to the route."""

    audio_source: AudioSource
    job_id: str


async def store_audio_source_for_session(
    db: AsyncSession,
    *,
    session: Session,
    upload_file: UploadFile,
    redis_client: Redis,
) -> AudioUploadResult:
    """Persist the audio file, transition state, and enqueue transcription.

    Pre-conditions checked here (raise domain exceptions on failure):
    - the upload MIME must be M4A-compatible (UnsupportedAudioMimeError)
    - the session must not already have an audio source (AudioAlreadyUploadedError)

    Side effects:
    - writes the file to ``KAEYRIS_DATA_DIR/audios/<session_id>.m4a``
    - INSERT into ``jdr_audio_sources``
    - UPDATE ``jdr_sessions.state`` to ``audio_uploaded``
    - enqueues ``transcribe_session_job`` (retryable)
    """
    if upload_file.content_type not in ACCEPTED_AUDIO_MIMES:
        raise UnsupportedAudioMimeError(
            f"Audio MIME {upload_file.content_type!r} is not an M4A."
        )

    repo = SessionRepository(db)
    existing = await repo.get_audio_source(session.id)
    if existing is not None:
        raise AudioAlreadyUploadedError(
            f"Session {session.id} already has an audio source."
        )

    target_path = (
        Path(settings.KAEYRIS_DATA_DIR) / "audios" / f"{session.id}.m4a"
    )
    target_path.parent.mkdir(parents=True, exist_ok=True)

    sha256 = hashlib.sha256()
    size_bytes = 0
    with target_path.open("wb") as dest:
        while True:
            chunk = await upload_file.read(_CHUNK_SIZE)
            if not chunk:
                break
            sha256.update(chunk)
            size_bytes += len(chunk)
            dest.write(chunk)

    duration_seconds = _probe_duration_seconds(target_path)

    audio = await repo.store_audio_source(
        session.id,
        path=str(target_path.relative_to(settings.KAEYRIS_DATA_DIR).as_posix()),
        sha256=sha256.hexdigest(),
        size_bytes=size_bytes,
        duration_seconds=duration_seconds,
    )
    await repo.update_state(session.id, SessionState.AUDIO_UPLOADED)

    queue = get_default_queue(redis_client)
    job = enqueue_job(
        queue, transcribe_session_job, session.id, transient_errors=True
    )

    return AudioUploadResult(audio_source=audio, job_id=job.id)


# ---------------------------------------------------------------------------
# Audio purge / reset (Lot 4b)
# ---------------------------------------------------------------------------


async def purge_audio_for_session(
    db: AsyncSession,
    *,
    session: Session,
) -> None:
    """Drop the audio file and reset the session to ``created``.

    Allowed states: ``audio_uploaded``, ``transcription_failed``. In both
    cases the file is still on disk (the auto-purge only fires on
    transcription success — FR-004) and the MJ wants either to cancel an
    upload mistake or to re-upload after a failure.

    Refused states:

    - ``transcribing``: a worker has the file; tolerating purge here would
      race the job. -> AudioPurgeBlockedError.
    - ``transcribed``: the audio was already auto-purged. Calling DELETE
      now is misleading — surface it explicitly. -> AudioPurgeBlockedError.
    - ``created`` and any other state without an audio source row
      -> NoAudioToPurgeError (404).

    Side effects on the happy path:

    - removes ``<KAEYRIS_DATA_DIR>/<audio.path>`` from disk if it exists
      (best effort — a stale file on disk is logged as a warning).
    - UPDATE ``jdr_audio_sources.purged_at`` so the row stays for audit.
    - UPDATE ``jdr_sessions.state = 'created'`` so a fresh upload is allowed.
    """
    if session.state == SessionState.TRANSCRIBING:
        raise AudioPurgeBlockedError(
            f"Session {session.id} is currently transcribing; "
            "purging the audio now would race the worker."
        )
    if session.state == SessionState.TRANSCRIBED:
        raise AudioPurgeBlockedError(
            f"Session {session.id} is already transcribed and its audio "
            "was auto-purged at that time — nothing left to delete."
        )

    repo = SessionRepository(db)
    audio = await repo.get_audio_source(session.id)
    if audio is None:
        raise NoAudioToPurgeError(
            f"Session {session.id} has no audio source on record."
        )

    if audio.purged_at is None:
        full_path = Path(settings.KAEYRIS_DATA_DIR) / audio.path
        try:
            full_path.unlink(missing_ok=True)
        except OSError as exc:
            # DB is the source of truth; janitor sweep can pick this up later.
            logger.warning(
                "Failed to delete audio file %s during purge: %s",
                full_path,
                exc,
            )

    await repo.mark_audio_purged(session.id)
    await repo.update_state(session.id, SessionState.CREATED)
    await db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _probe_duration_seconds(audio_path: Path) -> int | None:
    """Best-effort ``ffprobe`` invocation.

    Returns ``None`` whenever ``ffprobe`` is unavailable or fails; the
    caller stores ``NULL`` in that case. Documented as non-fatal in the
    spec (FR-004 only requires the audio to be transcribed, not that the
    duration be probed at upload).
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "json",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env={**os.environ},
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        logger.info("ffprobe unavailable, duration unknown: %s", exc)
        return None

    if result.returncode != 0:
        logger.info("ffprobe returned %d, duration unknown.", result.returncode)
        return None

    try:
        data = json.loads(result.stdout)
        return int(float(data["format"]["duration"]))
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.info("ffprobe output unparseable: %s", exc)
        return None
