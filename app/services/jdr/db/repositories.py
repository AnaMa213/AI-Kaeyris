"""Repository pattern for every JDR entity (ADR 0006 §5).

Encapsulates the SQLAlchemy queries so that ``logic.py`` calls readable
domain methods (``SessionRepository.list_for_gm(gm_key_id)``) instead
of building ``select(Session).where(...)`` everywhere. Repositories are
stateless: they hold an ``AsyncSession`` and nothing else.

This file declares the **shape** of each repository. Method bodies are
filled in by the user-story tasks that consume them (US1 -> sessions,
audio, transcription, narrative artefact ; US2 -> elements ; US3 ->
pjs, mappings, povs ; US4 -> player keys ; jobs throughout).

The convention for not-yet-implemented methods is ``NotImplementedError``
with a ``Filled in by USx.`` message so a missing implementation surfaces
as a clear runtime error rather than a silent no-op.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.exc import IntegrityError

from app.core.datetime_serialization import ensure_aware_utc
from app.services.jdr.db.models import (
    Artifact,
    AudioSource,
    Chunk,
    Campaign,
    CampaignMember,
    CampaignRole,
    Job,
    LocalModelCategory,
    LocalModelValidation,
    LocalModelValidationStatus,
    ModelProvider,
    ModelSettings,
    Pj,
    Session,
    SessionPjMapping,
    SessionPlayer,
    SessionState,
    Transcription,
    TranscriptionMode,
)

if TYPE_CHECKING:
    from app.services.jdr.db.models import ApiKey


class DuplicatePjNameError(Exception):
    """A PJ with this name already exists in this campaign.

    Raised by :meth:`PjRepository.create` when the
    ``(campaign_id, name)`` uniqueness constraint trips. The route
    maps this to HTTP 409.
    """


class DuplicateCampaignNameError(Exception):
    """A campaign with this normalized name already exists for this user."""


@dataclass(frozen=True, slots=True)
class CampaignSummary:
    campaign: Campaign
    role: CampaignRole
    session_count: int
    last_session_at: datetime | None


class _BaseRepository:
    """Carries the ``AsyncSession``; subclasses inherit it."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session


class ApiKeyRepository(_BaseRepository):
    """``jdr_api_keys`` access. Used by ``app/core/auth.py`` (already
    inlined there for performance) and by US4 for player enrolment."""

    async def list_active(self) -> list[ApiKey]:
        raise NotImplementedError("Inlined in app/core/auth.py for hot-path use.")

    async def find_by_id(self, api_key_id: UUID) -> ApiKey | None:
        raise NotImplementedError("Filled in by US4.")

    async def create_player(self, *, name: str, hash_: str, pj_id: UUID) -> ApiKey:
        raise NotImplementedError("Filled in by US4.")

    async def revoke(self, api_key_id: UUID) -> None:
        raise NotImplementedError("Filled in by US4.")


class CampaignRepository(_BaseRepository):
    """Campaign and membership access for BD-4 auth context."""

    async def user_has_campaign_named(
        self,
        *,
        user_id: UUID,
        name: str,
        exclude_campaign_id: UUID | None = None,
    ) -> bool:
        normalized = name.strip().lower()
        stmt = select(Campaign.id).where(
            Campaign.owner_user_id == user_id,
            func.lower(func.trim(Campaign.name)) == normalized,
        )
        if exclude_campaign_id is not None:
            stmt = stmt.where(Campaign.id != exclude_campaign_id)
        return (await self._session.scalar(stmt.limit(1))) is not None

    async def create(
        self,
        *,
        name: str,
        owner_user_id: UUID,
        description: str | None = None,
    ) -> Campaign:
        if await self.user_has_campaign_named(user_id=owner_user_id, name=name):
            raise DuplicateCampaignNameError(
                f"User already has a campaign named {name.strip()!r}."
            )
        row = Campaign(
            name=name.strip(),
            description=description,
            owner_user_id=owner_user_id,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get(self, campaign_id: UUID) -> Campaign | None:
        return await self._session.get(Campaign, campaign_id)

    def _summary_stats_subquery(self):
        return (
            select(
                Session.campaign_id.label("campaign_id"),
                func.count(Session.id).label("session_count"),
                func.max(Session.recorded_at).label("last_session_at"),
            )
            .group_by(Session.campaign_id)
            .subquery()
        )

    async def list_for_user(self, user_id: UUID) -> list[CampaignSummary]:
        stats = self._summary_stats_subquery()
        stmt = (
            select(
                Campaign,
                CampaignMember.role,
                func.coalesce(stats.c.session_count, 0),
                stats.c.last_session_at,
            )
            .join(CampaignMember, CampaignMember.campaign_id == Campaign.id)
            .outerjoin(stats, stats.c.campaign_id == Campaign.id)
            .where(CampaignMember.user_id == user_id)
            .order_by(Campaign.created_at, Campaign.name, Campaign.id)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            CampaignSummary(
                campaign=campaign,
                role=role,
                session_count=int(session_count),
                last_session_at=(
                    ensure_aware_utc(last_session_at)
                    if last_session_at is not None
                    else None
                ),
            )
            for campaign, role, session_count, last_session_at in rows
        ]

    async def get_summary_for_user(
        self,
        *,
        user_id: UUID,
        campaign_id: UUID,
    ) -> CampaignSummary | None:
        stats = self._summary_stats_subquery()
        stmt = (
            select(
                Campaign,
                CampaignMember.role,
                func.coalesce(stats.c.session_count, 0),
                stats.c.last_session_at,
            )
            .join(CampaignMember, CampaignMember.campaign_id == Campaign.id)
            .outerjoin(stats, stats.c.campaign_id == Campaign.id)
            .where(
                Campaign.id == campaign_id,
                CampaignMember.user_id == user_id,
            )
            .limit(1)
        )
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return None
        campaign, role, session_count, last_session_at = row
        return CampaignSummary(
            campaign=campaign,
            role=role,
            session_count=int(session_count),
            last_session_at=(
                ensure_aware_utc(last_session_at)
                if last_session_at is not None
                else None
            ),
        )

    async def update_campaign(
        self,
        *,
        campaign: Campaign,
        name: str | None = None,
        description: str | None = None,
        set_description: bool = False,
    ) -> Campaign:
        if name is not None:
            if await self.user_has_campaign_named(
                user_id=campaign.owner_user_id,
                name=name,
                exclude_campaign_id=campaign.id,
            ):
                raise DuplicateCampaignNameError(
                    f"User already has a campaign named {name.strip()!r}."
                )
            campaign.name = name.strip()
        if set_description:
            campaign.description = description
        await self._session.flush()
        await self._session.refresh(campaign)
        return campaign

    async def count_sessions(self, campaign_id: UUID) -> int:
        return int(
            await self._session.scalar(
                select(func.count(Session.id)).where(Session.campaign_id == campaign_id)
            )
            or 0
        )

    async def delete_campaign(self, campaign: Campaign) -> None:
        await self._session.delete(campaign)
        await self._session.flush()

    async def first(self) -> Campaign | None:
        return await self._session.scalar(
            select(Campaign).order_by(Campaign.created_at, Campaign.id).limit(1)
        )

    async def get_membership(
        self, *, user_id: UUID, campaign_id: UUID
    ) -> CampaignMember | None:
        return await self._session.get(
            CampaignMember,
            {"user_id": user_id, "campaign_id": campaign_id},
        )

    async def add_membership(
        self,
        *,
        user_id: UUID,
        campaign_id: UUID,
        role: CampaignRole,
        character_id: UUID | None = None,
    ) -> CampaignMember:
        row = CampaignMember(
            user_id=user_id,
            campaign_id=campaign_id,
            role=role,
            character_id=character_id,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_user_memberships(self, user_id: UUID) -> list[CampaignMember]:
        result = await self._session.scalars(
            select(CampaignMember)
            .where(CampaignMember.user_id == user_id)
            .order_by(CampaignMember.joined_at, CampaignMember.campaign_id)
        )
        return list(result.all())

    async def list_campaign_user_ids(self, campaign_id: UUID) -> list[UUID]:
        result = await self._session.scalars(
            select(CampaignMember.user_id).where(
                CampaignMember.campaign_id == campaign_id
            )
        )
        return list(result.all())


class PjRepository(_BaseRepository):
    """``jdr_pjs`` access. Used by US3 (mapping) and US4 (player listing)."""

    async def create(
        self,
        *,
        name: str,
        owner_gm_key_id: UUID,
        campaign_id: UUID,
        user_id: UUID | None = None,
    ) -> Pj:
        row = Pj(
            name=name,
            owner_gm_key_id=owner_gm_key_id,
            campaign_id=campaign_id,
            user_id=user_id,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # The (campaign_id, name) uniqueness constraint trips.
            # Roll back the partial flush so the caller's outer commit
            # doesn't choke on a poisoned session.
            await self._session.rollback()
            raise DuplicatePjNameError(
                f"This campaign already has a PJ named {name!r}."
            ) from exc
        await self._session.refresh(row)
        return row

    async def list_for_gm(
        self, gm_key_id: UUID, campaign_id: UUID | None = None
    ) -> list[Pj]:
        stmt = (
            select(Pj)
            .where(Pj.owner_gm_key_id == gm_key_id)
            .order_by(Pj.created_at)
        )
        if campaign_id is not None:
            stmt = stmt.where(Pj.campaign_id == campaign_id)
        result = await self._session.scalars(stmt)
        return list(result.all())

    async def list_for_member(
        self, *, user_id: UUID, campaign_id: UUID | None = None
    ) -> list[Pj]:
        stmt = (
            select(Pj)
            .join(CampaignMember, CampaignMember.campaign_id == Pj.campaign_id)
            .where(CampaignMember.user_id == user_id)
            .order_by(Pj.created_at, Pj.id)
        )
        if campaign_id is not None:
            stmt = stmt.where(Pj.campaign_id == campaign_id)
        result = await self._session.scalars(stmt)
        return list(result.all())

    async def find_by_id_owned_by(
        self, pj_id: UUID, gm_key_id: UUID, campaign_id: UUID | None = None
    ) -> Pj | None:
        stmt = select(Pj).where(
            Pj.id == pj_id, Pj.owner_gm_key_id == gm_key_id
        )
        if campaign_id is not None:
            stmt = stmt.where(Pj.campaign_id == campaign_id)
        return await self._session.scalar(stmt)

    async def flush_update(self, row: Pj) -> None:
        attempted_name = row.name
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise DuplicatePjNameError(
                f"This campaign already has a PJ named {attempted_name!r}."
            ) from exc


class SessionRepository(_BaseRepository):
    """``jdr_sessions`` + ``jdr_audio_sources`` (1-1 with sessions).

    Audio source operations are kept on the same repository because
    ``AudioSource`` is a value-object owned by ``Session`` (no business
    meaning outside its session)."""

    async def create(
        self,
        *,
        title: str,
        recorded_at: datetime,
        gm_key_id: UUID,
        campaign_id: UUID | None = None,
        campaign_context: str | None = None,
        transcription_mode: TranscriptionMode = TranscriptionMode.DIARISED,
    ) -> Session:
        row = Session(
            title=title,
            recorded_at=recorded_at,
            gm_key_id=gm_key_id,
            campaign_id=campaign_id,
            campaign_context=campaign_context,
            transcription_mode=transcription_mode,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def list_for_gm(
        self, gm_key_id: UUID, campaign_id: UUID | None = None
    ) -> list[Session]:
        stmt = (
            select(Session)
            .where(Session.gm_key_id == gm_key_id)
            .order_by(Session.created_at)
        )
        if campaign_id is not None:
            stmt = stmt.where(Session.campaign_id == campaign_id)
        result = await self._session.scalars(stmt)
        return list(result.all())

    async def get_for_gm(
        self,
        session_id: UUID,
        gm_key_id: UUID,
        campaign_id: UUID | None = None,
    ) -> Session | None:
        stmt = select(Session).where(
            Session.id == session_id,
            Session.gm_key_id == gm_key_id,
        )
        if campaign_id is not None:
            stmt = stmt.where(Session.campaign_id == campaign_id)
        return await self._session.scalar(stmt)

    async def store_audio_source(
        self,
        session_id: UUID,
        *,
        path: str,
        sha256: str,
        size_bytes: int,
        duration_seconds: int | None,
    ) -> AudioSource:
        row = AudioSource(
            session_id=session_id,
            path=path,
            sha256=sha256,
            size_bytes=size_bytes,
            duration_seconds=duration_seconds,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get_audio_source(self, session_id: UUID) -> AudioSource | None:
        return await self._session.scalar(
            select(AudioSource).where(AudioSource.session_id == session_id)
        )

    async def mark_audio_purged(self, session_id: UUID) -> None:
        from datetime import UTC, datetime

        await self._session.execute(
            update(AudioSource)
            .where(AudioSource.session_id == session_id)
            .values(purged_at=datetime.now(UTC))
        )

    async def update_audio_source_file(
        self,
        session_id: UUID,
        *,
        path: str,
        sha256: str,
        size_bytes: int,
        duration_seconds: int | None,
    ) -> AudioSource | None:
        audio = await self.get_audio_source(session_id)
        if audio is None:
            return None
        audio.path = path
        audio.sha256 = sha256
        audio.size_bytes = size_bytes
        audio.duration_seconds = duration_seconds
        await self._session.flush()
        await self._session.refresh(audio)
        return audio

    async def update_state(self, session_id: UUID, state: SessionState) -> None:
        await self._session.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(state=state)
        )

    async def set_current_job_id(
        self, session_id: UUID, job_id: str | None
    ) -> None:
        await self._session.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(current_job_id=job_id)
        )

    async def clear_current_job_id(self, session_id: UUID) -> None:
        await self.set_current_job_id(session_id, None)

    async def update_edited_transcript(
        self, session: Session, *, content_md: str
    ) -> Session:
        session.edited_transcript_md = content_md
        await self._session.flush()
        await self._session.refresh(session)
        return session

    async def clear_edited_transcript(self, session_id: UUID) -> None:
        await self._session.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(edited_transcript_md=None)
        )

    async def delete(self, session: Session) -> None:
        await self._session.delete(session)
        await self._session.flush()


class TranscriptionRepository(_BaseRepository):
    """``jdr_transcriptions`` access. Used by US1 (write) and every other
    US (read)."""

    async def upsert(
        self,
        session_id: UUID,
        *,
        segments: list[dict],
        language: str,
        model_used: str,
        provider: str,
    ) -> Transcription:
        from datetime import UTC, datetime

        existing = await self._session.scalar(
            select(Transcription).where(Transcription.session_id == session_id)
        )
        now = datetime.now(UTC)
        if existing is not None:
            existing.segments_json = segments
            existing.language = language
            existing.model_used = model_used
            existing.provider = provider
            existing.completed_at = now
            await self._session.flush()
            return existing
        row = Transcription(
            session_id=session_id,
            segments_json=segments,
            language=language,
            model_used=model_used,
            provider=provider,
            completed_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get_for_session(
        self, session_id: UUID
    ) -> Transcription | None:
        return await self._session.scalar(
            select(Transcription).where(Transcription.session_id == session_id)
        )

    async def delete_for_session(self, session_id: UUID) -> int:
        result = await self._session.execute(
            delete(Transcription).where(Transcription.session_id == session_id)
        )
        return result.rowcount


class MappingRepository(_BaseRepository):
    """``jdr_session_pj_mappings`` access (US3)."""

    async def get_for_session(
        self, session_id: UUID
    ) -> list[SessionPjMapping]:
        stmt = (
            select(SessionPjMapping)
            .where(SessionPjMapping.session_id == session_id)
            .order_by(SessionPjMapping.speaker_label)
        )
        result = await self._session.scalars(stmt)
        return list(result.all())

    async def replace_for_session(
        self,
        session_id: UUID,
        mapping: dict[str, UUID],
    ) -> list[SessionPjMapping]:
        """Replace the mapping atomically.

        Invalidating the matching ``pov:<pj_id>`` artefacts is the
        caller's responsibility (delegated to
        :meth:`ArtifactRepository.invalidate_pov_artifacts`) so the
        repository stays focused on a single table.
        """
        await self._session.execute(
            delete(SessionPjMapping).where(
                SessionPjMapping.session_id == session_id
            )
        )
        rows = [
            SessionPjMapping(
                session_id=session_id,
                speaker_label=label,
                pj_id=pj_id,
            )
            for label, pj_id in mapping.items()
        ]
        for row in rows:
            self._session.add(row)
        await self._session.flush()
        return rows


class ArtifactRepository(_BaseRepository):
    """``jdr_artifacts`` access. Composite PK on (session_id, kind),
    UPSERT semantics so a regeneration overwrites the previous content."""

    async def upsert(
        self,
        session_id: UUID,
        *,
        kind: str,
        content_json: dict,
        model_used: str,
    ) -> Artifact:
        from datetime import UTC, datetime

        existing = await self._session.scalar(
            select(Artifact).where(
                Artifact.session_id == session_id,
                Artifact.kind == kind,
            )
        )
        now = datetime.now(UTC)
        if existing is not None:
            existing.content_json = content_json
            existing.model_used = model_used
            existing.generated_at = now
            await self._session.flush()
            return existing
        row = Artifact(
            session_id=session_id,
            kind=kind,
            content_json=content_json,
            model_used=model_used,
            generated_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get(self, session_id: UUID, kind: str) -> Artifact | None:
        return await self._session.scalar(
            select(Artifact).where(
                Artifact.session_id == session_id,
                Artifact.kind == kind,
            )
        )

    async def list_for_session(self, session_id: UUID) -> list[Artifact]:
        rows = await self._session.scalars(
            select(Artifact).where(Artifact.session_id == session_id)
        )
        return list(rows.all())

    async def invalidate_pov_artifacts(self, session_id: UUID) -> int:
        """Delete every ``pov:*`` row for this session; called when the
        mapping changes (data-model.md §6 invariant).

        Returns the number of deleted rows so the caller can log it.
        """
        result = await self._session.execute(
            delete(Artifact).where(
                Artifact.session_id == session_id,
                Artifact.kind.like("pov:%"),
            )
        )
        return result.rowcount

    async def delete_for_session(self, session_id: UUID) -> int:
        result = await self._session.execute(
            delete(Artifact).where(Artifact.session_id == session_id)
        )
        return result.rowcount


class JobRepository(_BaseRepository):
    """``jdr_jobs`` lightweight projection of RQ jobs (data-model.md §8)."""

    async def upsert_status(
        self,
        job_id: str,
        *,
        kind,
        session_id: UUID,
        status,
        failure_reason: str | None = None,
    ) -> Job:
        now = datetime.now(UTC)
        existing = await self._session.get(Job, job_id)
        if existing is not None:
            existing.kind = kind
            existing.session_id = session_id
            existing.status = status
            existing.failure_reason = failure_reason
            if status.value == "running" and existing.started_at is None:
                existing.started_at = now
            if status.value in {"succeeded", "failed"}:
                existing.ended_at = now
            await self._session.flush()
            return existing

        row = Job(
            id=job_id,
            kind=kind,
            session_id=session_id,
            status=status,
            failure_reason=failure_reason,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_for_session(self, session_id: UUID) -> list[Job]:
        rows = await self._session.scalars(
            select(Job).where(Job.session_id == session_id).order_by(Job.queued_at)
        )
        return list(rows.all())

    async def get(self, job_id: str) -> Job | None:
        return await self._session.get(Job, job_id)


# ---------------------------------------------------------------------------
# Sous-jalon 5.5 — feature 002-non-diarised-mode
# ---------------------------------------------------------------------------


class ChunkRepository(_BaseRepository):
    """``jdr_chunks`` access. Used by the transcription job (non_diarised
    branch) to seed chunks, by `_generate_summary` to populate
    `summary_text` per chunk, and by the dérivés narrative/elements/povs
    jobs to read the summaries back."""

    async def bulk_create_for_session(
        self, session_id: UUID, *, texts: list[str]
    ) -> list[Chunk]:
        """Insert one row per text, ordered by index (`ordre = i`).

        Atomic at the flush level — caller controls the outer commit.
        """
        rows = [
            Chunk(session_id=session_id, ordre=i, text=text)
            for i, text in enumerate(texts)
        ]
        for row in rows:
            self._session.add(row)
        await self._session.flush()
        return rows

    async def list_for_session(self, session_id: UUID) -> list[Chunk]:
        stmt = (
            select(Chunk)
            .where(Chunk.session_id == session_id)
            .order_by(Chunk.ordre)
        )
        result = await self._session.scalars(stmt)
        return list(result.all())

    async def update_summary_text(
        self, chunk_id: UUID, *, summary_text: str
    ) -> None:
        await self._session.execute(
            update(Chunk)
            .where(Chunk.id == chunk_id)
            .values(summary_text=summary_text)
        )

    async def reset_summary_texts(self, session_id: UUID) -> int:
        """Set every `summary_text` to NULL for this session.

        Called at the start of `_generate_summary` to invalidate stale
        per-chunk summaries (FR-011). Returns the number of rows reset.
        """
        result = await self._session.execute(
            update(Chunk)
            .where(Chunk.session_id == session_id)
            .values(summary_text=None)
        )
        return result.rowcount

    async def delete_for_session(self, session_id: UUID) -> int:
        result = await self._session.execute(
            delete(Chunk).where(Chunk.session_id == session_id)
        )
        return result.rowcount


class SessionPlayerRepository(_BaseRepository):
    """``jdr_session_players`` access. Used by the `povs` job (non_diarised
    branch) to know which PJ to produce a POV for."""

    async def list_for_session(self, session_id: UUID) -> list[SessionPlayer]:
        stmt = (
            select(SessionPlayer)
            .where(SessionPlayer.session_id == session_id)
            .order_by(SessionPlayer.created_at)
        )
        result = await self._session.scalars(stmt)
        return list(result.all())

    async def replace_for_session(
        self, session_id: UUID, *, pj_ids: list[UUID]
    ) -> list[SessionPlayer]:
        """Replace the whole player list atomically (DELETE + INSERT)."""
        from sqlalchemy import delete

        await self._session.execute(
            delete(SessionPlayer).where(SessionPlayer.session_id == session_id)
        )
        # De-duplicate while preserving order
        seen: set[UUID] = set()
        unique_ids: list[UUID] = []
        for pj_id in pj_ids:
            if pj_id not in seen:
                seen.add(pj_id)
                unique_ids.append(pj_id)
        rows = [
            SessionPlayer(session_id=session_id, pj_id=pj_id)
            for pj_id in unique_ids
        ]
        for row in rows:
            self._session.add(row)
        await self._session.flush()
        return rows


class LocalModelValidationRepository(_BaseRepository):
    """Short-lived local model validation proof access (BD-20)."""

    async def create_succeeded(
        self,
        *,
        validation_hash: str,
        user_id: UUID,
        category: LocalModelCategory,
        model_path: str,
        path_hash: str,
        runtime: str,
        model_format: str,
        message: str,
        expires_at: datetime,
    ) -> LocalModelValidation:
        row = LocalModelValidation(
            validation_hash=validation_hash,
            user_id=user_id,
            category=category,
            model_path=model_path,
            path_hash=path_hash,
            status=LocalModelValidationStatus.SUCCEEDED,
            runtime=runtime,
            model_format=model_format,
            message=message,
            expires_at=expires_at,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get_by_hash(self, validation_hash: str) -> LocalModelValidation | None:
        return await self._session.get(LocalModelValidation, validation_hash)


class ModelSettingsRepository(_BaseRepository):
    """Per-web-user AI provider settings (BD-18 / FR-22)."""

    async def get_for_user(self, user_id: UUID) -> ModelSettings | None:
        return await self._session.get(ModelSettings, user_id)

    async def upsert_for_user(
        self,
        *,
        user_id: UUID,
        transcription_provider: ModelProvider | None = None,
        summary_provider: ModelProvider | None = None,
        transcription_local_path: str | None = None,
        summary_local_path: str | None = None,
        transcription_cloud_model: str | None = None,
        summary_cloud_model: str | None = None,
        ollama_model: str | None = None,
        transcription_local_validation_hash: str | None = None,
        summary_local_validation_hash: str | None = None,
        deepinfra_api_key: str | None = None,
    ) -> ModelSettings:
        row = await self.get_for_user(user_id)
        if row is None:
            row = ModelSettings(user_id=user_id)
            self._session.add(row)

        if transcription_provider is not None:
            row.transcription_provider = transcription_provider
        if summary_provider is not None:
            row.summary_provider = summary_provider
        if transcription_local_path is not None:
            row.transcription_local_path = transcription_local_path
        if summary_local_path is not None:
            row.summary_local_path = summary_local_path
        if transcription_cloud_model is not None:
            row.transcription_cloud_model = transcription_cloud_model
        if summary_cloud_model is not None:
            row.summary_cloud_model = summary_cloud_model
        if ollama_model is not None:
            row.ollama_model = ollama_model
        if transcription_local_validation_hash is not None:
            row.transcription_local_validation_hash = (
                transcription_local_validation_hash
            )
        if summary_local_validation_hash is not None:
            row.summary_local_validation_hash = summary_local_validation_hash
        # Write-only secret: only store when a non-empty key is provided; an
        # empty string means "keep the existing key" (the frontend never sends
        # an empty value to clear it).
        if deepinfra_api_key:
            row.deepinfra_api_key = deepinfra_api_key

        await self._session.flush()
        await self._session.refresh(row)
        return row
