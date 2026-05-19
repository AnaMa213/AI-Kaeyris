"""Common Pydantic v2 schemas shared by every JDR endpoint.

Per-user-story schemas (``SessionCreate``, ``SessionOut``, ``PjCreate``,
…) are added incrementally by the corresponding US tasks; this module
holds only the **transverse** shapes:

- ``JobOut`` : projection of ``app.services.jdr.db.models.Job`` for the
  ``GET /jobs/{id}`` endpoint, used by every async-producing route.
- ``Page[T]`` : generic envelope for paginated list endpoints.

Error responses do not live here — they follow RFC 9457 Problem Details
emitted by ``app.core.errors`` (jalon 1, ADR 0002 §3).
"""

from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.services.jdr.db.models import (
    JobKind,
    JobStatus,
    SessionMode,
    SessionState,
    TranscriptionMode,
)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Sessions (US1)
# ---------------------------------------------------------------------------


class SessionCreate(BaseModel):
    """Payload accepted by ``POST /services/jdr/sessions``."""

    title: str = Field(..., min_length=1, max_length=500)
    recorded_at: datetime = Field(
        ...,
        description=(
            "When the session actually took place (not the upload time). "
            "ISO-8601 with timezone."
        ),
    )
    transcription_mode: TranscriptionMode | None = Field(
        default=None,
        description=(
            "Optional. 'diarised' (default, Jalon 5 behaviour) or "
            "'non_diarised' (chunked transcription + map-reduce LLM "
            "summary). Immutable after creation. Default applied "
            "server-side if omitted."
        ),
    )
    campaign_context: str | None = Field(
        default=None,
        max_length=8000,
        description=(
            "Optional campaign-bible block prepended to every narrative / "
            "elements LLM prompt for this session. Use it to anchor the "
            "model on recurring PNJ, the campaign tone, or the current "
            "story arc. Can be updated later via PATCH."
        ),
    )


class SessionUpdate(BaseModel):
    """Payload accepted by ``PATCH /services/jdr/sessions/{id}``.

    Every field is optional — the route applies only the keys that are
    present (PATCH semantics). To clear ``campaign_context``, send
    ``"campaign_context": null`` explicitly (the route distinguishes
    "unset" from "explicit null" via Pydantic's ``model_fields_set``).
    """

    title: str | None = Field(default=None, min_length=1, max_length=500)
    campaign_context: str | None = Field(default=None, max_length=8000)


class SessionOut(BaseModel):
    """Public projection of ``jdr_sessions`` rows."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    recorded_at: datetime
    mode: SessionMode
    state: SessionState
    transcription_mode: TranscriptionMode
    campaign_context: str | None = None
    created_at: datetime
    updated_at: datetime


class AudioUploadOut(BaseModel):
    """Response of ``POST /sessions/{id}/audio``."""

    model_config = ConfigDict(from_attributes=True)

    session_id: UUID
    path: str = Field(
        ..., description="Path on disk relative to KAEYRIS_DATA_DIR."
    )
    sha256: str
    size_bytes: int
    duration_seconds: int | None = Field(
        None,
        description=(
            "Audio duration in seconds. ``null`` when ``ffprobe`` is "
            "unavailable on the host — non-fatal."
        ),
    )
    uploaded_at: datetime
    job_id: str = Field(
        ..., description="RQ job identifier for the queued transcription."
    )


# ---------------------------------------------------------------------------
# Transcription (US1)
# ---------------------------------------------------------------------------


class TranscriptionSegmentOut(BaseModel):
    """One diarised utterance in a transcription."""

    speaker_label: str = Field(
        ...,
        description=(
            "Raw label produced by the backend ('speaker_1', 'unknown', …). "
            "The mapping to PJs is a per-session business decision exposed "
            "via /mapping (US3)."
        ),
    )
    start_seconds: float
    end_seconds: float
    text: str


class TranscriptionOut(BaseModel):
    """Public projection of ``jdr_transcriptions`` rows."""

    session_id: UUID
    segments: list[TranscriptionSegmentOut]
    language: str = Field(..., description="BCP-47 code, e.g. 'fr'.")
    model_used: str
    provider: str = Field(..., description="'cloud' | 'local' | 'mock'.")
    completed_at: datetime


# ---------------------------------------------------------------------------
# PJ — Personnages-joueurs (US3)
# ---------------------------------------------------------------------------


class PjCreate(BaseModel):
    """Payload accepted by ``POST /services/jdr/pjs``."""

    name: str = Field(..., min_length=1, max_length=255)


class PjOut(BaseModel):
    """Public projection of ``jdr_pjs`` rows."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Speaker ↔ PJ mapping (US3)
# ---------------------------------------------------------------------------


class MappingPut(BaseModel):
    """Body for ``PUT /services/jdr/sessions/{session_id}/mapping``.

    Shape ``{speaker_label: pj_id}`` per ``contracts/rest-api.md``
    §148-163.
    """

    mapping: dict[str, UUID] = Field(
        default_factory=dict,
        description="speaker_label -> pj_id",
    )


class MappingOut(BaseModel):
    """Response for both PUT and GET ``/mapping``.

    ``updated_at`` is the most recent ``updated_at`` across the rows,
    or ``None`` when the session has no mapping yet.
    """

    session_id: UUID
    mapping: dict[str, UUID]
    updated_at: datetime | None


# ---------------------------------------------------------------------------
# Narrative artefact (US1)
# ---------------------------------------------------------------------------


class NarrativeArtifactOut(BaseModel):
    """Public projection of an ``Artifact(kind='narrative')`` row."""

    session_id: UUID
    text: str = Field(..., description="French narrative summary produced by the LLM.")
    model_used: str
    generated_at: datetime


# ---------------------------------------------------------------------------
# POV artefact (US3)
# ---------------------------------------------------------------------------


class PovArtifactOut(BaseModel):
    """Public projection of an ``Artifact(kind='pov:<pj_id>')`` row.

    ``pj_id`` is exposed at the top level for clients that don't want to
    parse the composite ``kind`` field.
    """

    session_id: UUID
    pj_id: UUID
    text: str = Field(..., description="French POV summary scoped to this PJ.")
    model_used: str
    generated_at: datetime


# ---------------------------------------------------------------------------
# Player enrolment (US4)
# ---------------------------------------------------------------------------


class PlayerCreate(BaseModel):
    """Body for ``POST /services/jdr/players``."""

    name: str = Field(..., min_length=1, max_length=255)
    pj_id: UUID


class PlayerOut(BaseModel):
    """Response of ``POST /services/jdr/players``.

    ``token`` is the *plaintext* Bearer token; it is returned **once** at
    creation and never again. The server only stores the Argon2 hash.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    pj_id: UUID
    token: str = Field(
        ...,
        description=(
            "Plaintext Bearer token. Shown only on this 201 response. "
            "Store it now — the server only keeps an Argon2 hash."
        ),
    )
    created_at: datetime


class PjMini(BaseModel):
    """Compact PJ projection used inside ``MeOut``."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str


class MeOut(BaseModel):
    """Response of ``GET /services/jdr/me``."""

    name: str
    pj: PjMini


class PlayerSessionItem(BaseModel):
    """One row of ``GET /services/jdr/me/sessions``."""

    session_id: UUID
    title: str
    recorded_at: datetime


class PlayerSessionListOut(BaseModel):
    """Envelope for ``GET /services/jdr/me/sessions``."""

    items: list[PlayerSessionItem]


# ---------------------------------------------------------------------------
# Elements artefact (US2)
# ---------------------------------------------------------------------------


class Element(BaseModel):
    """One row of the four-category elements card.

    The LLM may return arbitrary keys; we surface only ``name`` and
    ``description`` so the public contract is stable across model swaps.
    """

    name: str = Field(..., description="Short label (proper name or descriptor).")
    description: str = Field(
        "", description="One-sentence description (≤ ~25 words)."
    )


class ElementsArtifactOut(BaseModel):
    """Public projection of an ``Artifact(kind='elements')`` row.

    The four lists are *always* present, even when empty (``[]``). See
    acceptance scenario US 2.3 in ``spec.md``.
    """

    session_id: UUID
    npcs: list[Element] = Field(default_factory=list)
    locations: list[Element] = Field(default_factory=list)
    items: list[Element] = Field(default_factory=list)
    clues: list[Element] = Field(default_factory=list)
    model_used: str
    generated_at: datetime


class JobQueuedOut(BaseModel):
    """Reply to an artefact-trigger POST: a freshly enqueued RQ job.

    Status starts at ``queued``; the worker updates it as it processes.
    The full lifecycle (running -> succeeded/failed) is observable via
    GET /jobs/{id} (added in sub-lot 3f).
    """

    id: str = Field(..., description="RQ job identifier.")
    kind: JobKind
    session_id: UUID
    status: JobStatus = Field(JobStatus.QUEUED, description="Initial status after enqueue.")
    queued_at: datetime


class JobOut(BaseModel):
    """Job status projection — see ``data-model.md`` §8."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="RQ job identifier (echoed from the queue).")
    kind: JobKind
    session_id: UUID
    status: JobStatus
    failure_reason: str | None = None
    queued_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None


class Page(BaseModel, Generic[T]):
    """Generic paginated envelope.

    Conventions:
    - ``total`` is the full match count (independent of pagination).
    - ``page`` is 1-based.
    - ``size`` is the page size; the server may cap it.
    """

    items: list[T]
    total: int = Field(..., ge=0)
    page: int = Field(1, ge=1)
    size: int = Field(50, ge=1, le=500)


# ---------------------------------------------------------------------------
# Sous-jalon 5.5 — feature 002-non-diarised-mode
# ---------------------------------------------------------------------------


class ChunkOut(BaseModel):
    """One row of `jdr_chunks` as exposed by `GET /sessions/{id}/chunks`.

    `summary_text` is intentionally NOT exposed — it is internal to the
    LLM pipeline (research.md §5).
    """

    model_config = ConfigDict(from_attributes=True)

    chunk_id: UUID = Field(..., validation_alias="id")
    ordre: int
    text: str


class ChunkListOut(BaseModel):
    """Response of `GET /services/jdr/sessions/{session_id}/chunks`."""

    session_id: UUID
    items: list[ChunkOut]


class SummaryArtifactOut(BaseModel):
    """Public projection of an ``Artifact(kind='summary')`` row."""

    session_id: UUID
    text: str = Field(..., description="Global session summary in French.")
    model_used: str
    generated_at: datetime


class SessionPlayersIn(BaseModel):
    """Body of `POST /services/jdr/sessions/{session_id}/players`.

    Replaces the player list integrally (PUT-like semantics).
    """

    pj_ids: list[UUID] = Field(..., min_length=1, max_length=50)


class SessionPlayersOut(BaseModel):
    """Response of `POST` and `GET /sessions/{session_id}/players`."""

    session_id: UUID
    pj_ids: list[UUID]
    updated_at: datetime | None
