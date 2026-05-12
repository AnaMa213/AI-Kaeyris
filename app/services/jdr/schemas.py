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

from app.services.jdr.db.models import JobKind, JobStatus, SessionMode, SessionState

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


class SessionOut(BaseModel):
    """Public projection of ``jdr_sessions`` rows."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    recorded_at: datetime
    mode: SessionMode
    state: SessionState
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
