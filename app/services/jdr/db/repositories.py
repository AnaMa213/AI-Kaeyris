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

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.jdr.db.models import AudioSource, Session, SessionState

if TYPE_CHECKING:
    from app.services.jdr.db.models import (
        ApiKey,
        Artifact,
        Job,
        Pj,
        SessionPjMapping,
        Transcription,
    )


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


class PjRepository(_BaseRepository):
    """``jdr_pjs`` access. Used by US3 (mapping) and US4 (player listing)."""

    async def create(self, *, name: str, owner_gm_key_id: UUID) -> Pj:
        raise NotImplementedError("Filled in by US3.")

    async def list_for_gm(self, gm_key_id: UUID) -> list[Pj]:
        raise NotImplementedError("Filled in by US3.")

    async def find_by_id_owned_by(
        self, pj_id: UUID, gm_key_id: UUID
    ) -> Pj | None:
        raise NotImplementedError("Filled in by US3.")


class SessionRepository(_BaseRepository):
    """``jdr_sessions`` + ``jdr_audio_sources`` (1-1 with sessions).

    Audio source operations are kept on the same repository because
    ``AudioSource`` is a value-object owned by ``Session`` (no business
    meaning outside its session)."""

    async def create(
        self, *, title: str, recorded_at: datetime, gm_key_id: UUID
    ) -> Session:
        row = Session(
            title=title,
            recorded_at=recorded_at,
            gm_key_id=gm_key_id,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def list_for_gm(self, gm_key_id: UUID) -> list[Session]:
        stmt = (
            select(Session)
            .where(Session.gm_key_id == gm_key_id)
            .order_by(Session.created_at)
        )
        result = await self._session.scalars(stmt)
        return list(result.all())

    async def get_for_gm(
        self, session_id: UUID, gm_key_id: UUID
    ) -> Session | None:
        stmt = select(Session).where(
            Session.id == session_id,
            Session.gm_key_id == gm_key_id,
        )
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

    async def update_state(self, session_id: UUID, state: SessionState) -> None:
        await self._session.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(state=state)
        )


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
        raise NotImplementedError("Filled in by US1.")

    async def get_for_session(
        self, session_id: UUID
    ) -> Transcription | None:
        raise NotImplementedError("Filled in by US1.")


class MappingRepository(_BaseRepository):
    """``jdr_session_pj_mappings`` access (US3)."""

    async def get_for_session(
        self, session_id: UUID
    ) -> list[SessionPjMapping]:
        raise NotImplementedError("Filled in by US3.")

    async def replace_for_session(
        self,
        session_id: UUID,
        mapping: dict[str, UUID],
    ) -> None:
        """Replace the mapping atomically and invalidate the matching
        ``pov:<pj_id>`` artefacts (delegated to ``ArtifactRepository``)."""
        raise NotImplementedError("Filled in by US3.")


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
        raise NotImplementedError("Filled in by US1/US2/US3.")

    async def get(self, session_id: UUID, kind: str) -> Artifact | None:
        raise NotImplementedError("Filled in by US1.")

    async def list_for_session(self, session_id: UUID) -> list[Artifact]:
        raise NotImplementedError("Filled in by US1.")

    async def invalidate_pov_artifacts(self, session_id: UUID) -> int:
        """Delete every ``pov:*`` row for this session; called when the
        mapping changes (data-model.md §6 invariant)."""
        raise NotImplementedError("Filled in by US3.")


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
        raise NotImplementedError("Filled in by US1.")

    async def list_for_session(self, session_id: UUID) -> list[Job]:
        raise NotImplementedError("Filled in by US1.")

    async def get(self, job_id: str) -> Job | None:
        raise NotImplementedError("Filled in by US1.")
