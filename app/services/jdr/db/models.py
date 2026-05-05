"""SQLAlchemy 2.x ORM models for the JDR service.

ADR 0006 §1 + spec ``data-model.md``. Eight tables prefixed ``jdr_*`` so
the same database can host other services later without name clashes.

Implementation choices kept simple on purpose:
- ``Uuid`` from SQLAlchemy 2.x is portable (CHAR(32) on SQLite, native
  UUID on Postgres). Defaults to ``uuid.uuid4`` for client-side generation.
- ``DateTime(timezone=True)`` everywhere; values inserted as ``datetime.now(UTC)``.
- ``JSON`` for transcription segments and artefact contents — SQLite uses
  the JSON1 extension, Postgres will use JSONB transparently.
- Cross-table invariants (role/pj_id consistency, purge-after-transcription,
  etc.) are enforced in business code, not via DB CHECK constraints. The
  reasoning: they involve multiple tables / lifecycle states, and SQLite's
  CHECK is single-row only — keeping the DB layer simple avoids a layer of
  divergence between SQLite and Postgres.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Role(str, enum.Enum):
    """Auth role borne by an API key (data-model.md §1)."""

    GM = "gm"
    PLAYER = "player"


class ApiKeyStatus(str, enum.Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


class SessionMode(str, enum.Enum):
    BATCH = "batch"
    LIVE = "live"  # reserved for future use; jalon 5 always writes "batch"


class SessionState(str, enum.Enum):
    """Lifecycle of a session — see data-model.md §3."""

    CREATED = "created"
    AUDIO_UPLOADED = "audio_uploaded"
    TRANSCRIBING = "transcribing"
    TRANSCRIPTION_FAILED = "transcription_failed"
    TRANSCRIBED = "transcribed"


class JobKind(str, enum.Enum):
    TRANSCRIPTION = "transcription"
    NARRATIVE = "narrative"
    ELEMENTS = "elements"
    POVS = "povs"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Timezone-aware UTC default — never use naive datetimes in this project."""
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


class ApiKey(Base):
    """Auth key — extends jalon 2's env-var registry to a DB-backed table.

    See data-model.md §1 and ADR 0006 §3.
    """

    __tablename__ = "jdr_api_keys"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role, name="jdr_role"), nullable=False)
    pj_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        # use_alter=True breaks the FK cycle (api_keys.pj_id -> pjs.id and
        # pjs.owner_gm_key_id -> api_keys.id). SQLAlchemy creates both tables
        # first, then adds this constraint via ALTER TABLE.
        ForeignKey(
            "jdr_pjs.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_jdr_api_keys_pj_id_jdr_pjs",
        ),
        nullable=True,
    )
    status: Mapped[ApiKeyStatus] = mapped_column(
        Enum(ApiKeyStatus, name="jdr_api_key_status"),
        nullable=False,
        default=ApiKeyStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationship: a player key points to its PJ.
    pj: Mapped[Pj | None] = relationship("Pj", foreign_keys=[pj_id])


class Pj(Base):
    """A character (Personnage Joueur) — stable across sessions."""

    __tablename__ = "jdr_pjs"
    __table_args__ = (
        UniqueConstraint("owner_gm_key_id", "name", name="owner_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_gm_key_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("jdr_api_keys.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    owner: Mapped[ApiKey] = relationship("ApiKey", foreign_keys=[owner_gm_key_id])


class Session(Base):
    """A JDR session — the central business entity."""

    __tablename__ = "jdr_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    gm_key_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("jdr_api_keys.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    mode: Mapped[SessionMode] = mapped_column(
        Enum(SessionMode, name="jdr_session_mode"),
        nullable=False,
        default=SessionMode.BATCH,
    )
    state: Mapped[SessionState] = mapped_column(
        Enum(SessionState, name="jdr_session_state"),
        nullable=False,
        default=SessionState.CREATED,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    gm: Mapped[ApiKey] = relationship("ApiKey", foreign_keys=[gm_key_id])
    audio_source: Mapped[AudioSource | None] = relationship(
        "AudioSource", back_populates="session", uselist=False, cascade="all, delete-orphan"
    )
    transcription: Mapped[Transcription | None] = relationship(
        "Transcription", back_populates="session", uselist=False, cascade="all, delete-orphan"
    )
    mappings: Mapped[list[SessionPjMapping]] = relationship(
        "SessionPjMapping", back_populates="session", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list[Artifact]] = relationship(
        "Artifact", back_populates="session", cascade="all, delete-orphan"
    )
    jobs: Mapped[list[Job]] = relationship(
        "Job", back_populates="session", cascade="all, delete-orphan"
    )


class AudioSource(Base):
    """Uploaded audio file metadata — purged from disk after transcription."""

    __tablename__ = "jdr_audio_sources"

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("jdr_sessions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    purged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    session: Mapped[Session] = relationship("Session", back_populates="audio_source")


class Transcription(Base):
    """Diarised transcription output — JSON-stored segments."""

    __tablename__ = "jdr_transcriptions"

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("jdr_sessions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    segments_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False)
    model_used: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    session: Mapped[Session] = relationship("Session", back_populates="transcription")


class SessionPjMapping(Base):
    """``speaker_label → pj_id`` mapping per session.

    Editable as long as no POV artefact has been generated for the corresponding
    PJ (see data-model.md §6 invariant). When the mapping changes, the matching
    ``artifacts(kind='pov:<pj_id>')`` rows are invalidated by business code.
    """

    __tablename__ = "jdr_session_pj_mappings"

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("jdr_sessions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    speaker_label: Mapped[str] = mapped_column(String(64), primary_key=True)
    pj_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("jdr_pjs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    session: Mapped[Session] = relationship("Session", back_populates="mappings")
    pj: Mapped[Pj] = relationship("Pj", foreign_keys=[pj_id])


class Artifact(Base):
    """Generated artefact: narrative, elements, or pov:<pj_id>.

    Composite primary key ``(session_id, kind)`` guarantees that a new
    generation overwrites the previous one (UPSERT semantics in business code).
    """

    __tablename__ = "jdr_artifacts"

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("jdr_sessions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # `kind` is "narrative", "elements", or "pov:<pj_uuid>"; we keep it as
    # a free-form string rather than an enum because POV kinds are dynamic.
    kind: Mapped[str] = mapped_column(String(80), primary_key=True)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    model_used: Mapped[str] = mapped_column(String(255), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    session: Mapped[Session] = relationship("Session", back_populates="artifacts")


class Job(Base):
    """Lightweight projection of an RQ job for cross-table queries.

    Source of truth remains Redis (RQ). This table simplifies "show me the
    jobs for this session" queries and survives RQ TTL expiration.
    """

    __tablename__ = "jdr_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[JobKind] = mapped_column(
        Enum(JobKind, name="jdr_job_kind"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("jdr_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="jdr_job_status"),
        nullable=False,
        default=JobStatus.QUEUED,
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    session: Mapped[Session] = relationship("Session", back_populates="jobs")
