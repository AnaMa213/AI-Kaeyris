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
from typing import Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.core.datetime_serialization import ensure_aware_utc, serialize_datetime_utc

from app.services.jdr.db.models import (
    JobKind,
    JobStatus,
    LocalModelCategory,
    LocalModelValidationStatus,
    ModelProvider,
    SessionMode,
    SessionState,
    TranscriptionMode,
)

T = TypeVar("T")


class JdrSchema(BaseModel):
    """Base schema for JDR JSON output conventions."""

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_datetimes(self, value):
        if isinstance(value, datetime):
            return serialize_datetime_utc(value)
        return value


# ---------------------------------------------------------------------------
# Campaigns (BD-6)
# ---------------------------------------------------------------------------


class CampaignCreate(JdrSchema):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Campaign name cannot be blank.")
        return stripped


class CampaignPatch(JdrSchema):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("Campaign name cannot be blank.")
        return stripped


class CampaignOut(JdrSchema):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None = None
    role: Literal["gm", "pj"]
    session_count: int
    last_session_at: datetime | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Account-level AI model settings (BD-18 / FR-22)
# ---------------------------------------------------------------------------


class ModelSettingsOut(JdrSchema):
    transcription_provider: ModelProvider = Field(
        ModelProvider.CLOUD,
        description="Provider for transcription: cloud, local, or ollama.",
    )
    summary_provider: ModelProvider = Field(
        ModelProvider.CLOUD,
        description="Provider for LLM summary: cloud, local, or ollama.",
    )
    transcription_local_path: str | None = Field(
        None,
        max_length=1024,
        description="Custom local model path used when transcription_provider is local.",
    )
    summary_local_path: str | None = Field(
        None,
        max_length=1024,
        description="Custom local model path used when summary_provider is local.",
    )
    transcription_cloud_model: str | None = Field(
        None,
        max_length=200,
        description="DeepInfra cloud model id used when transcription_provider is cloud.",
    )
    summary_cloud_model: str | None = Field(
        None,
        max_length=200,
        description="DeepInfra cloud model id used when summary_provider is cloud.",
    )
    ollama_model: str | None = Field(
        None,
        max_length=200,
        description="Ollama model name used when summary_provider is ollama.",
    )
    deepinfra_api_key_set: bool = Field(
        False,
        description=(
            "True iff a DeepInfra API key is stored for this user. The key "
            "itself is never returned."
        ),
    )


class ModelSettingsPatch(JdrSchema):
    transcription_provider: ModelProvider | None = None
    summary_provider: ModelProvider | None = None
    transcription_local_path: str | None = Field(None, max_length=1024)
    summary_local_path: str | None = Field(None, max_length=1024)
    transcription_local_validation_id: str | None = Field(None, max_length=128)
    summary_local_validation_id: str | None = Field(None, max_length=128)
    transcription_cloud_model: str | None = Field(None, max_length=200)
    summary_cloud_model: str | None = Field(None, max_length=200)
    ollama_model: str | None = Field(None, max_length=200)
    deepinfra_api_key: str | None = Field(
        None,
        max_length=512,
        description=(
            "Write-only. When provided non-empty, stores/replaces the user's "
            "DeepInfra API key. Never serialized back in any response."
        ),
    )


class LocalModelValidationRequest(JdrSchema):
    category: LocalModelCategory
    model_path: str = Field(..., min_length=1, max_length=1024)

    @field_validator("model_path")
    @classmethod
    def reject_blank_model_path(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Local model path cannot be blank.")
        return stripped


class LocalModelValidationOut(JdrSchema):
    validation_id: str
    category: LocalModelCategory
    model_path: str = Field(..., max_length=1024)
    status: LocalModelValidationStatus
    runtime: str
    model_format: str
    message: str
    expires_at: datetime

    @field_serializer("expires_at", when_used="json")
    def serialize_expires_at(self, value: datetime) -> str:
        return serialize_datetime_utc(value).replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Sessions (US1)
# ---------------------------------------------------------------------------


class SessionCreate(JdrSchema):
    """Payload accepted by ``POST /services/jdr/sessions``."""

    title: str = Field(..., min_length=1, max_length=500)
    campaign_id: UUID
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

    @field_validator("recorded_at")
    @classmethod
    def normalize_recorded_at(cls, value: datetime) -> datetime:
        return ensure_aware_utc(value)


class SessionUpdate(JdrSchema):
    """Payload accepted by ``PATCH /services/jdr/sessions/{id}``.

    Every field is optional — the route applies only the keys that are
    present (PATCH semantics). To clear ``campaign_context``, send
    ``"campaign_context": null`` explicitly (the route distinguishes
    "unset" from "explicit null" via Pydantic's ``model_fields_set``).
    """

    title: str | None = Field(default=None, min_length=1, max_length=500)
    campaign_context: str | None = Field(default=None, max_length=8000)


class SessionOut(JdrSchema):
    """Public projection of ``jdr_sessions`` rows."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    recorded_at: datetime
    mode: SessionMode
    state: SessionState
    transcription_mode: TranscriptionMode
    campaign_context: str | None = None
    current_job_id: str | None = None
    created_at: datetime
    updated_at: datetime


class AudioUploadOut(JdrSchema):
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


class TranscriptionSegmentOut(JdrSchema):
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


class TranscriptionOut(JdrSchema):
    """Public projection of ``jdr_transcriptions`` rows."""

    session_id: UUID
    segments: list[TranscriptionSegmentOut]
    language: str = Field(..., description="BCP-47 code, e.g. 'fr'.")
    model_used: str
    provider: str = Field(..., description="'cloud' | 'local' | 'mock'.")
    completed_at: datetime


class TranscriptionEditIn(JdrSchema):
    """Payload accepted by ``PUT /services/jdr/sessions/{id}/transcription``."""

    content_md: str = Field(..., min_length=1)

    @field_validator("content_md")
    @classmethod
    def reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Edited transcription cannot be blank.")
        return value


class TranscriptionEditOut(JdrSchema):
    """Projection returned after persisting an edited transcription."""

    session_id: UUID
    content_md: str
    is_edited: bool = True
    updated_at: datetime


# ---------------------------------------------------------------------------
# PJ — Personnages-joueurs (US3)
# ---------------------------------------------------------------------------


class PjCreate(JdrSchema):
    """Payload accepted by ``POST /services/jdr/pjs``."""

    name: str = Field(..., min_length=1, max_length=255)
    campaign_id: UUID | None = None
    user_id: UUID | None = None


class PjUpdate(JdrSchema):
    """Partial payload accepted by ``PATCH /services/jdr/pjs/{pj_id}``."""

    name: str = Field(default=None, min_length=1, max_length=255)
    user_id: UUID | None = None


class PjOut(JdrSchema):
    """Public projection of ``jdr_pjs`` rows."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    campaign_id: UUID
    user_id: UUID | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Speaker ↔ PJ mapping (US3)
# ---------------------------------------------------------------------------


class MappingPut(JdrSchema):
    """Body for ``PUT /services/jdr/sessions/{session_id}/mapping``.

    Shape ``{speaker_label: pj_id}`` per ``contracts/rest-api.md``
    §148-163.
    """

    mapping: dict[str, UUID] = Field(
        default_factory=dict,
        description="speaker_label -> pj_id",
    )


class MappingOut(JdrSchema):
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


class NarrativeArtifactOut(JdrSchema):
    """Public projection of an ``Artifact(kind='narrative')`` row."""

    session_id: UUID
    text: str = Field(..., description="French narrative summary produced by the LLM.")
    model_used: str
    generated_at: datetime


# ---------------------------------------------------------------------------
# POV artefact (US3)
# ---------------------------------------------------------------------------


class PovArtifactOut(JdrSchema):
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


class PlayerCreate(JdrSchema):
    """Body for ``POST /services/jdr/players``."""

    name: str = Field(..., min_length=1, max_length=255)
    pj_id: UUID


class PlayerOut(JdrSchema):
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


class PjMini(JdrSchema):
    """Compact PJ projection used inside ``MeOut``."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str


class MeOut(JdrSchema):
    """Response of ``GET /services/jdr/me``."""

    name: str
    pj: PjMini


class PlayerSessionItem(JdrSchema):
    """One row of ``GET /services/jdr/me/sessions``."""

    session_id: UUID
    title: str
    recorded_at: datetime


class PlayerSessionListOut(JdrSchema):
    """Envelope for ``GET /services/jdr/me/sessions``."""

    items: list[PlayerSessionItem]


# ---------------------------------------------------------------------------
# Elements artefact (US2)
# ---------------------------------------------------------------------------


class Element(JdrSchema):
    """One row of the four-category elements card.

    The LLM may return arbitrary keys; we surface only ``name`` and
    ``description`` so the public contract is stable across model swaps.
    """

    name: str = Field(..., description="Short label (proper name or descriptor).")
    description: str = Field(
        "", description="One-sentence description (≤ ~25 words)."
    )


class ElementsArtifactOut(JdrSchema):
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


class JobQueuedOut(JdrSchema):
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


class JobOut(JdrSchema):
    """Job status projection — see ``data-model.md`` §8.

    ``phase`` and ``progress_percent`` (BD-10) are best-effort transcription
    progress read from the RQ job metadata. They are nullable on purpose:
    queued jobs, missing/expired/malformed metadata, and non-transcription
    jobs all map to ``null`` so the contract never breaks for a valid job.
    ``status`` stays the authoritative lifecycle field.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="RQ job identifier (echoed from the queue).")
    kind: JobKind
    session_id: UUID
    status: JobStatus
    failure_reason: str | None = None
    queued_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    phase: Literal["reducing", "transcribing", "done", "failed"] | None = Field(
        default=None,
        description=(
            "Best-effort transcription phase. ``null`` when unknown, not "
            "started, expired, or for non-transcription jobs. ``queued`` is "
            "intentionally absent — use ``status`` for that."
        ),
    )
    progress_percent: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description=(
            "Best-effort transcription progress (0..100). ``null`` when "
            "unknown/not started/expired. ``100`` is reserved for terminal "
            "success paired with ``phase=\"done\"``."
        ),
    )


class Page(JdrSchema, Generic[T]):
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


class ChunkOut(JdrSchema):
    """One row of `jdr_chunks` as exposed by `GET /sessions/{id}/chunks`.

    `summary_text` is intentionally NOT exposed — it is internal to the
    LLM pipeline (research.md §5).
    """

    model_config = ConfigDict(from_attributes=True)

    chunk_id: UUID = Field(..., validation_alias="id")
    ordre: int
    text: str


class ChunkListOut(JdrSchema):
    """Response of `GET /services/jdr/sessions/{session_id}/chunks`."""

    session_id: UUID
    items: list[ChunkOut]


class SummaryArtifactOut(JdrSchema):
    """Public projection of an ``Artifact(kind='summary')`` row."""

    session_id: UUID
    text: str = Field(..., description="Global session summary in French.")
    model_used: str
    generated_at: datetime


class SessionPlayersIn(JdrSchema):
    """Body of `POST /services/jdr/sessions/{session_id}/players`.

    Replaces the player list integrally (PUT-like semantics).
    """

    pj_ids: list[UUID] = Field(..., min_length=1, max_length=50)


class SessionPlayersOut(JdrSchema):
    """Response of `POST` and `GET /sessions/{session_id}/players`."""

    session_id: UUID
    pj_ids: list[UUID]
    updated_at: datetime | None
