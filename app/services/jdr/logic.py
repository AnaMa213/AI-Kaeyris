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
import shutil
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
    JobKind,
    JobStatus,
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
    JobRepository,
    MappingRepository,
    PjRepository,
    SessionPlayerRepository,
    SessionRepository,
    TranscriptionRepository,
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


class PjCampaignResolutionError(Exception):
    """No explicit or default campaign could be resolved for PJ creation."""


class PjForbiddenError(Exception):
    """The current user is not GM/member of the requested PJ campaign."""


class PjAssignmentError(Exception):
    """The optional assigned user does not exist."""


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

    ``state == transcribing`` is the only refused state: a worker may have
    the file open and be writing derived rows.
    """


class NoAudioToPurgeError(Exception):
    """The session has no audio source on record — nothing to delete."""


class AudioReadNotFoundError(Exception):
    """The session audio cannot be served."""


class AudioUploadTooLargeError(Exception):
    """The uploaded audio exceeded the configured raw upload limit."""

    def __init__(self, *, limit_bytes: int) -> None:
        super().__init__(
            f"Audio upload exceeds the configured limit of {limit_bytes} bytes."
        )
        self.limit_bytes = limit_bytes


class SessionNotTranscribedForEditError(Exception):
    """A transcription edit was attempted before transcription completed."""


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
    from app.core.models import User

    user = await db.get(User, owner_user_id)
    if user is not None and user.default_campaign_id is None:
        user.default_campaign_id = campaign.id
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


async def get_session_for_transcription_edit(
    db: AsyncSession,
    *,
    session_id: UUID,
    gm_key_id: UUID,
    campaign_id: UUID | None = None,
) -> Session | None:
    return await SessionRepository(db).get_for_gm(
        session_id, gm_key_id, campaign_id
    )


async def save_session_transcription_edit(
    db: AsyncSession,
    *,
    session: Session,
    content_md: str,
) -> Session:
    if session.state != SessionState.TRANSCRIBED:
        raise SessionNotTranscribedForEditError(
            f"Session {session.id} is not transcribed."
        )
    updated = await SessionRepository(db).update_edited_transcript(
        session, content_md=content_md
    )
    await db.commit()
    await db.refresh(updated)
    return updated


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
        if campaign_id is not None:
            stmt = stmt.where(Pj.campaign_id == campaign_id)
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


@dataclass(frozen=True, slots=True)
class AudioReadResult:
    """Resolved audio file metadata for HTTP streaming."""

    path: Path
    size_bytes: int
    media_type: str = "audio/mp4"


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
    - writes the raw file to
      ``KAEYRIS_DATA_DIR/.tmp/audio-reduce/<session_id>/raw.m4a``
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

    target_path = raw_audio_path_for_session(session.id)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    sha256 = hashlib.sha256()
    size_bytes = 0
    try:
        with target_path.open("wb") as dest:
            while True:
                chunk = await upload_file.read(_CHUNK_SIZE)
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > settings.KAEYRIS_AUDIO_MAX_UPLOAD_BYTES:
                    raise AudioUploadTooLargeError(
                        limit_bytes=settings.KAEYRIS_AUDIO_MAX_UPLOAD_BYTES
                    )
                sha256.update(chunk)
                dest.write(chunk)
    except AudioUploadTooLargeError:
        best_effort_unlink(
            target_path,
            event="audio.upload_too_large_unlink_failed",
        )
        best_effort_remove_tree(
            target_path.parent,
            event="audio.upload_too_large_rmtree_failed",
        )
        raise

    duration_seconds = _probe_duration_seconds(target_path)

    audio = await repo.store_audio_source(
        session.id,
        path=_relative_data_path(target_path),
        sha256=sha256.hexdigest(),
        size_bytes=size_bytes,
        duration_seconds=duration_seconds,
    )
    await repo.update_state(session.id, SessionState.AUDIO_UPLOADED)

    queue = get_default_queue(redis_client)
    job = enqueue_job(
        queue, transcribe_session_job, session.id, transient_errors=True
    )
    await JobRepository(db).upsert_status(
        job.id,
        kind=JobKind.TRANSCRIPTION,
        session_id=session.id,
        status=JobStatus.QUEUED,
    )
    await repo.set_current_job_id(session.id, job.id)

    return AudioUploadResult(audio_source=audio, job_id=job.id)


async def get_audio_for_session(
    db: AsyncSession,
    *,
    session: Session,
) -> AudioReadResult:
    """Resolve a non-purged source audio file for playback/download."""
    audio = await SessionRepository(db).get_audio_source(session.id)
    if audio is None:
        raise AudioReadNotFoundError(
            f"Session {session.id} has no audio source on record."
        )
    if audio.purged_at is not None:
        raise AudioReadNotFoundError(f"Session {session.id} audio is purged.")

    full_path = Path(settings.KAEYRIS_DATA_DIR) / audio.path
    if not full_path.is_file():
        raise AudioReadNotFoundError(
            f"Session {session.id} audio file is missing on disk."
        )

    return AudioReadResult(
        path=full_path,
        size_bytes=full_path.stat().st_size,
    )


# ---------------------------------------------------------------------------
# PJ — Personnages-joueurs (US3 — sub-lot 5a)
# ---------------------------------------------------------------------------


async def create_pj(
    db: AsyncSession,
    *,
    name: str,
    gm_key_id: UUID,
    user_id: UUID | None = None,
    campaign_id: UUID | None = None,
    requester_user_id: UUID | None = None,
) -> Pj:
    """Create a PJ scoped to the current MJ.

    Raises :class:`DuplicatePjError` if the MJ already has a PJ with the
    same name (uniqueness ``(owner_gm_key_id, name)`` on
    :class:`Pj`).
    """
    resolved_campaign_id = campaign_id
    if requester_user_id is not None:
        from app.core.models import User
        from app.services.jdr.campaign_context import require_campaign_gm

        requester = await db.get(User, requester_user_id)
        if requester is None:
            raise PjForbiddenError("A web user is required to create a PJ.")
        resolved_campaign_id = resolved_campaign_id or requester.default_campaign_id
        if resolved_campaign_id is None:
            raise PjCampaignResolutionError(
                "No campaign_id was provided and the user has no default campaign."
            )
        await require_campaign_gm(
            db,
            user_id=requester_user_id,
            campaign_id=resolved_campaign_id,
        )

    if resolved_campaign_id is None:
        raise PjCampaignResolutionError("A campaign_id is required to create a PJ.")

    if user_id is not None:
        from app.core.models import User

        assigned_user = await db.get(User, user_id)
        if assigned_user is None:
            raise PjAssignmentError(f"User {user_id} not found.")

    try:
        pj = await PjRepository(db).create(
            name=name,
            owner_gm_key_id=gm_key_id,
            campaign_id=resolved_campaign_id,
            user_id=user_id,
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


async def update_pj(
    db: AsyncSession,
    *,
    pj_id: UUID,
    gm_key_id: UUID,
    name: str | None = None,
    user_id: UUID | None = None,
    update_name: bool = False,
    update_user_id: bool = False,
    campaign_id: UUID | None = None,
    requester_user_id: UUID | None = None,
) -> Pj | None:
    """Partially update an owned PJ.

    ``update_user_id`` preserves the PATCH distinction between an omitted
    field and an explicit ``null`` value, which unlinks the user.
    """
    repo = PjRepository(db)
    pj = await repo.find_by_id_owned_by(pj_id, gm_key_id, campaign_id)
    if pj is None:
        return None

    if requester_user_id is not None:
        from app.services.jdr.campaign_context import require_campaign_gm

        await require_campaign_gm(
            db,
            user_id=requester_user_id,
            campaign_id=pj.campaign_id,
        )

    if update_user_id and user_id is not None:
        from app.core.models import User

        assigned_user = await db.get(User, user_id)
        if assigned_user is None:
            raise PjAssignmentError(f"User {user_id} not found.")

    if update_name:
        pj.name = name
    if update_user_id:
        pj.user_id = user_id

    try:
        await repo.flush_update(pj)
    except DuplicatePjNameError as exc:
        raise DuplicatePjError(str(exc)) from exc
    await db.commit()
    await db.refresh(pj)
    return pj


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
    stmt = select(Pj).where(Pj.id == pj_id)
    if campaign_id is not None:
        stmt = stmt.where(Pj.campaign_id == campaign_id)
    return await db.scalar(stmt)


async def list_pjs(
    db: AsyncSession,
    *,
    gm_key_id: UUID,
    campaign_id: UUID | None = None,
    requester_user_id: UUID | None = None,
) -> list[Pj]:
    if requester_user_id is not None:
        from app.services.jdr.campaign_context import require_campaign_membership

        if campaign_id is not None:
            await require_campaign_membership(
                db,
                user_id=requester_user_id,
                campaign_id=campaign_id,
            )
        return await PjRepository(db).list_for_member(
            user_id=requester_user_id,
            campaign_id=campaign_id,
        )
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
        if campaign_id is not None:
            stmt = stmt.where(Pj.campaign_id == campaign_id)
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
    """Drop source audio and every output derived from it.

    The operation is intentionally idempotent for existing sessions. It
    refuses only ``transcribing`` because a worker may still be reading the
    file and writing rows.
    """
    if session.state == SessionState.TRANSCRIBING:
        raise AudioPurgeBlockedError(
            f"Session {session.id} is currently transcribing; "
            "purging the audio now would race the worker."
        )
    repo = SessionRepository(db)
    audio = await repo.get_audio_source(session.id)

    if audio is not None and audio.purged_at is None:
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

    best_effort_remove_tree(
        _raw_audio_reduce_dir(session.id),
        event="audio.purge_raw_rmtree_failed",
    )
    await TranscriptionRepository(db).delete_for_session(session.id)
    await ChunkRepository(db).delete_for_session(session.id)
    await ArtifactRepository(db).delete_for_session(session.id)
    await repo.clear_edited_transcript(session.id)
    await repo.clear_current_job_id(session.id)
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


def _data_dir() -> Path:
    return Path(settings.KAEYRIS_DATA_DIR)


def _relative_data_path(path: Path) -> str:
    return path.relative_to(_data_dir()).as_posix()


def _raw_audio_reduce_dir(session_id: UUID) -> Path:
    return _data_dir() / ".tmp" / "audio-reduce" / str(session_id)


def raw_audio_path_for_session(session_id: UUID) -> Path:
    return _raw_audio_reduce_dir(session_id) / "raw.m4a"


def prepared_audio_path_for_session(session_id: UUID) -> Path:
    return _data_dir() / "audios" / f"{session_id}.m4a"


def best_effort_unlink(path: Path, *, event: str) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(event, path=str(path), error=str(exc))


def best_effort_remove_tree(path: Path, *, event: str) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError as exc:
        logger.warning(event, path=str(path), error=str(exc))
