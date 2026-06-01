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
import os
import secrets
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from argon2 import PasswordHasher
from redis import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.core.config import settings
from app.core.logging import get_logger
from app.jobs import enqueue_job, get_default_queue
from app.jobs.jdr import transcribe_session_job
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    AudioSource,
    Chunk,
    CampaignRole,
    Pj,
    Role,
    Session,
    SessionPjMapping,
    SessionState,
    TranscriptionMode,
)
from app.services.jdr.db.repositories import (
    ArtifactRepository,
    CampaignRepository,
    CampaignSummary,
    ChunkRepository,
    DuplicateCampaignNameError,
    DuplicatePjNameError,
    MappingRepository,
    PjRepository,
    SessionPlayerRepository,
    SessionRepository,
)

logger = get_logger(__name__)

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


class DuplicatePjError(Exception):
    """A PJ with this name already exists for this MJ.

    Surface for the route layer — wraps the repository-level
    :class:`DuplicatePjNameError` so the route only knows about
    ``app.services.jdr.logic`` exceptions (separation of layers).
    """


class InvalidMappingError(Exception):
    """At least one ``pj_id`` in the mapping is unknown or owned by another MJ.

    Surfaced by :func:`set_session_mapping`. The route maps this to
    HTTP 422 ``invalid-mapping``.
    """


class InvalidPlayerError(Exception):
    """The PJ referenced in a player enrolment does not belong to the current MJ.

    Surfaced by :func:`enroll_player`. The route maps this to HTTP 422
    ``invalid-player``.
    """


class InvalidPlayerListError(Exception):
    """At least one pj_id in a `POST /players` body is unknown or foreign.

    Surfaced by :func:`set_session_players` (feature 002 — FR-012).
    The route maps this to HTTP 422 ``invalid-player-list``.
    """


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
# Campaigns (BD-6)
# ---------------------------------------------------------------------------


class CampaignNotFoundError(Exception):
    """The campaign does not exist."""


class CampaignForbiddenError(Exception):
    """The current user is not allowed to access the campaign."""


class DuplicateCampaignError(Exception):
    """The current user already has a campaign with this name."""


class CampaignHasSessionsError(Exception):
    """The campaign cannot be deleted because it still has sessions."""


async def list_campaigns(db: AsyncSession, *, user_id: UUID) -> list[CampaignSummary]:
    return await CampaignRepository(db).list_for_user(user_id)


async def get_campaign(
    db: AsyncSession,
    *,
    campaign_id: UUID,
    user_id: UUID,
) -> CampaignSummary:
    repo = CampaignRepository(db)
    campaign = await repo.get(campaign_id)
    if campaign is None:
        raise CampaignNotFoundError(f"Campaign {campaign_id} not found.")
    summary = await repo.get_summary_for_user(user_id=user_id, campaign_id=campaign_id)
    if summary is None:
        raise CampaignForbiddenError("User is not a member of this campaign.")
    return summary


async def create_campaign(
    db: AsyncSession,
    *,
    owner_user_id: UUID,
    name: str,
    description: str | None = None,
) -> CampaignSummary:
    repo = CampaignRepository(db)
    try:
        campaign = await repo.create(
            name=name,
            description=description,
            owner_user_id=owner_user_id,
        )
    except DuplicateCampaignNameError as exc:
        raise DuplicateCampaignError(str(exc)) from exc
    await repo.add_membership(
        user_id=owner_user_id,
        campaign_id=campaign.id,
        role=CampaignRole.GM,
    )
    await db.commit()
    summary = await repo.get_summary_for_user(
        user_id=owner_user_id,
        campaign_id=campaign.id,
    )
    if summary is None:
        raise CampaignForbiddenError("Created campaign membership is missing.")
    return summary


async def update_campaign(
    db: AsyncSession,
    *,
    campaign_id: UUID,
    user_id: UUID,
    name: str | None = None,
    description: str | None = None,
    set_description: bool = False,
) -> CampaignSummary:
    repo = CampaignRepository(db)
    campaign = await repo.get(campaign_id)
    if campaign is None:
        raise CampaignNotFoundError(f"Campaign {campaign_id} not found.")
    membership = await repo.get_membership(user_id=user_id, campaign_id=campaign_id)
    if membership is None or membership.role is not CampaignRole.GM:
        raise CampaignForbiddenError("User is not GM of this campaign.")
    try:
        await repo.update_campaign(
            campaign=campaign,
            name=name,
            description=description,
            set_description=set_description,
        )
    except DuplicateCampaignNameError as exc:
        raise DuplicateCampaignError(str(exc)) from exc
    await db.commit()
    summary = await repo.get_summary_for_user(user_id=user_id, campaign_id=campaign_id)
    if summary is None:
        raise CampaignForbiddenError("Updated campaign membership is missing.")
    return summary


async def delete_campaign(
    db: AsyncSession,
    *,
    campaign_id: UUID,
    user_id: UUID,
) -> None:
    repo = CampaignRepository(db)
    campaign = await repo.get(campaign_id)
    if campaign is None:
        raise CampaignNotFoundError(f"Campaign {campaign_id} not found.")
    membership = await repo.get_membership(user_id=user_id, campaign_id=campaign_id)
    if membership is None or membership.role is not CampaignRole.GM:
        raise CampaignForbiddenError("User is not GM of this campaign.")
    if await repo.count_sessions(campaign_id) > 0:
        raise CampaignHasSessionsError("Cannot delete a campaign with sessions.")
    await repo.delete_campaign(campaign)
    await db.commit()


# ---------------------------------------------------------------------------
# Sessions (CRUD already exposed at jalon 5 sub-lot 3a)
# ---------------------------------------------------------------------------


async def create_session(
    db: AsyncSession,
    *,
    title: str,
    recorded_at: datetime,
    gm_key_id: UUID,
    campaign_id: UUID | None = None,
    campaign_context: str | None = None,
    transcription_mode: TranscriptionMode = TranscriptionMode.DIARISED,
) -> Session:
    return await SessionRepository(db).create(
        title=title,
        recorded_at=recorded_at,
        gm_key_id=gm_key_id,
        campaign_id=campaign_id,
        campaign_context=campaign_context,
        transcription_mode=transcription_mode,
    )


async def list_session_chunks(
    db: AsyncSession, *, session: Session
) -> list[Chunk]:
    """Liste les chunks d'une session non_diarised, ordonnés par ``ordre``.

    Le caller a déjà validé l'ownership et le mode (sinon le list est
    sémantiquement bizarre — une session diarised n'aura jamais de chunks).
    """
    return await ChunkRepository(db).list_for_session(session.id)


# ---------------------------------------------------------------------------
# Session players (feature 002 — non_diarised, FR-012)
# ---------------------------------------------------------------------------


async def set_session_players(
    db: AsyncSession,
    *,
    session: Session,
    pj_ids: list[UUID],
    gm_key_id: UUID,
    campaign_id: UUID | None = None,
) -> list[UUID]:
    """Remplace la liste des PJ présents pour une session non_diarised.

    Valide que chaque ``pj_id`` appartient au MJ courant (sinon
    :class:`InvalidPlayerListError`). Renvoie la liste finale persistée
    (dédupliquée, ordre d'apparition préservé).
    """
    if pj_ids:
        unique_ids = set(pj_ids)
        # Vérifie ownership en batch
        stmt = select(Pj.id).where(
            Pj.id.in_(unique_ids), Pj.owner_gm_key_id == gm_key_id
        )
        found = set((await db.execute(stmt)).scalars().all())
        missing = unique_ids - found
        if missing:
            raise InvalidPlayerListError(
                f"Unknown or foreign PJ id(s): {sorted(str(m) for m in missing)}"
            )

    rows = await SessionPlayerRepository(db).replace_for_session(
        session.id, pj_ids=pj_ids
    )
    await db.commit()
    return [r.pj_id for r in rows]


async def list_session_players(
    db: AsyncSession, *, session: Session
) -> list[UUID]:
    """Renvoie les ``pj_id`` des PJ présents à la session, ordre de création."""
    rows = await SessionPlayerRepository(db).list_for_session(session.id)
    return [r.pj_id for r in rows]


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
    db: AsyncSession, *, gm_key_id: UUID, campaign_id: UUID | None = None
) -> list[Session]:
    return await SessionRepository(db).list_for_gm(gm_key_id, campaign_id)


async def get_session(
    db: AsyncSession,
    *,
    session_id: UUID,
    gm_key_id: UUID,
    campaign_id: UUID | None = None,
) -> Session | None:
    return await SessionRepository(db).get_for_gm(
        session_id, gm_key_id, campaign_id
    )


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
# PJ — Personnages-joueurs (US3 — sub-lot 5a)
# ---------------------------------------------------------------------------


async def create_pj(
    db: AsyncSession,
    *,
    name: str,
    gm_key_id: UUID,
    campaign_id: UUID | None = None,
) -> Pj:
    """Create a PJ scoped to the current MJ.

    Raises :class:`DuplicatePjError` if the MJ already has a PJ with the
    same name (uniqueness ``(owner_gm_key_id, name)`` on
    :class:`Pj`).
    """
    try:
        pj = await PjRepository(db).create(
            name=name,
            owner_gm_key_id=gm_key_id,
            campaign_id=campaign_id,
        )
    except DuplicatePjNameError as exc:
        raise DuplicatePjError(str(exc)) from exc
    await db.commit()
    await db.refresh(pj)
    return pj


# ---------------------------------------------------------------------------
# Player keys (US4 — sub-lot 6)
# ---------------------------------------------------------------------------


@dataclass
class EnrollPlayerResult:
    """Carries the freshly inserted row plus the *plaintext* token.

    The token is exposed only here, then discarded — the DB stores its
    Argon2 hash. Callers must surface it once to the operator and never
    keep it.
    """

    api_key: ApiKey
    plaintext_token: str


async def enroll_player(
    db: AsyncSession,
    *,
    name: str,
    pj_id: UUID,
    gm_key_id: UUID,
    campaign_id: UUID | None = None,
) -> EnrollPlayerResult:
    """Create a player API key bound to one of the GM's PJs.

    Validates that ``pj_id`` belongs to ``gm_key_id`` (FR-014) — raises
    :class:`InvalidPlayerError` otherwise. Generates a fresh URL-safe
    random token (≥ 32 bytes of entropy), hashes it with Argon2, and
    inserts a ``role='player'`` row.
    """
    pj = await PjRepository(db).find_by_id_owned_by(
        pj_id, gm_key_id, campaign_id
    )
    if pj is None:
        raise InvalidPlayerError(
            f"PJ {pj_id} is unknown or owned by another MJ."
        )

    plaintext = secrets.token_urlsafe(32)
    api_key = ApiKey(
        name=name,
        hash=PasswordHasher().hash(plaintext),
        role=Role.PLAYER,
        status=ApiKeyStatus.ACTIVE,
        pj_id=pj_id,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    return EnrollPlayerResult(api_key=api_key, plaintext_token=plaintext)


async def revoke_player(
    db: AsyncSession,
    *,
    player_id: UUID,
    gm_key_id: UUID,
    campaign_id: UUID | None = None,
) -> bool:
    """Revoke a player key.

    Returns ``True`` if the row was flipped to ``revoked`` (and the GM
    owned the bound PJ); ``False`` if the row doesn't exist or doesn't
    belong to this GM (route maps both to 404 to avoid probing).
    """
    stmt = (
        select(ApiKey)
        .join(Pj, Pj.id == ApiKey.pj_id)
        .where(
            ApiKey.id == player_id,
            ApiKey.role == Role.PLAYER,
            Pj.owner_gm_key_id == gm_key_id,
        )
    )
    _ = campaign_id
    row = await db.scalar(stmt)
    if row is None:
        return False
    row.status = ApiKeyStatus.REVOKED
    row.revoked_at = datetime.now(UTC)
    await db.commit()
    return True


async def list_player_sessions(
    db: AsyncSession, *, player_pj_id: UUID, campaign_id: UUID | None = None
) -> list[Session]:
    """Return the sessions where the player's PJ is mapped (FR-014).

    Anything not in this list is invisible to the player.
    """
    stmt = (
        select(Session)
        .join(SessionPjMapping, SessionPjMapping.session_id == Session.id)
        .where(SessionPjMapping.pj_id == player_pj_id)
        .order_by(Session.created_at)
        .distinct()
    )
    if campaign_id is not None:
        stmt = stmt.where(Session.campaign_id == campaign_id)
    rows = await db.scalars(stmt)
    return list(rows.all())


async def is_pj_mapped_on_session(
    db: AsyncSession,
    *,
    session_id: UUID,
    pj_id: UUID,
    campaign_id: UUID | None = None,
) -> bool:
    """True iff the (session_id, *, pj_id) row exists in the mapping."""
    stmt = (
        select(SessionPjMapping.session_id)
        .join(Session, Session.id == SessionPjMapping.session_id)
        .where(
            SessionPjMapping.session_id == session_id,
            SessionPjMapping.pj_id == pj_id,
        )
        .limit(1)
    )
    if campaign_id is not None:
        stmt = stmt.where(Session.campaign_id == campaign_id)
    return (await db.scalar(stmt)) is not None


async def get_player_pj(
    db: AsyncSession, *, pj_id: UUID, campaign_id: UUID | None = None
) -> Pj | None:
    """Load the PJ row referenced by a player key (for GET /me)."""
    _ = campaign_id
    stmt = select(Pj).where(Pj.id == pj_id)
    return await db.scalar(stmt)


async def list_pjs(
    db: AsyncSession, *, gm_key_id: UUID, campaign_id: UUID | None = None
) -> list[Pj]:
    return await PjRepository(db).list_for_gm(gm_key_id, campaign_id)


# ---------------------------------------------------------------------------
# Speaker ↔ PJ mapping (US3 — sub-lot 5a)
# ---------------------------------------------------------------------------


@dataclass
class MappingResult:
    """Snapshot of a session's speaker↔PJ mapping after a read or write."""

    mapping: dict[str, UUID]
    updated_at: datetime | None


async def get_session_mapping(
    db: AsyncSession, *, session_id: UUID
) -> MappingResult:
    rows = await MappingRepository(db).get_for_session(session_id)
    return MappingResult(
        mapping={r.speaker_label: r.pj_id for r in rows},
        updated_at=max((r.updated_at for r in rows), default=None),
    )


async def set_session_mapping(
    db: AsyncSession,
    *,
    session: Session,
    mapping: dict[str, UUID],
    gm_key_id: UUID,
    campaign_id: UUID | None = None,
) -> MappingResult:
    """Replace the session's speaker→PJ mapping atomically.

    Validates that every ``pj_id`` in ``mapping`` is owned by
    ``gm_key_id`` (raises :class:`InvalidMappingError` otherwise).
    Side-effect: deletes every ``artifacts(kind LIKE 'pov:%')`` row
    for the session — POVs must be regenerated explicitly after a
    mapping change (data-model.md §6 invariant, rest-api.md §169).
    """
    if mapping:
        unique_pj_ids = set(mapping.values())
        stmt = select(Pj.id).where(
            Pj.id.in_(unique_pj_ids),
            Pj.owner_gm_key_id == gm_key_id,
        )
        found_ids = set((await db.execute(stmt)).scalars().all())
        missing = unique_pj_ids - found_ids
        if missing:
            raise InvalidMappingError(
                "Unknown or foreign PJ id(s): "
                + ", ".join(sorted(str(m) for m in missing))
            )

    await MappingRepository(db).replace_for_session(session.id, mapping)
    await ArtifactRepository(db).invalidate_pov_artifacts(session.id)
    await db.commit()

    return await get_session_mapping(db, session_id=session.id)


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
                "audio.purge_unlink_failed",
                audio_path=str(full_path),
                error=str(exc),
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
        logger.info("ffprobe.unavailable", error=str(exc))
        return None

    if result.returncode != 0:
        logger.info("ffprobe.nonzero_exit", returncode=result.returncode)
        return None

    try:
        data = json.loads(result.stdout)
        return int(float(data["format"]["duration"]))
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.info("ffprobe.unparseable", error=str(exc))
        return None
