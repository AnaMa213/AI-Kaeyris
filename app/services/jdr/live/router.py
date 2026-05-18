"""Live-mode sub-router (stub at Jalon 5 — FR-015 / FR-016).

Two endpoints are published so the public contract is discoverable, but
neither carries a real implementation:

- ``POST /live/sessions`` always returns 501 with a Problem Details body
  whose ``type`` URI ends in ``errors/live-not-implemented``.
- ``WS /live/stream`` accepts the connection then immediately closes it
  with WebSocket code 1011 (Internal Error).

Future message schema (documented here in comments so it surfaces in
OpenAPI's WebSocket description):

- Client -> Server
  - ``audio.chunk`` ``{"chunk_index": int, "audio_base64": str, "sample_rate": int}``
    A short PCM/Opus chunk produced by the recorder.
  - ``session.end`` ``{}`` Signals the end of the live session and
    triggers final transcription stitching server-side.
- Server -> Client
  - ``transcript.partial`` ``{"speaker_label": str, "text": str, "t0": float, "t1": float}``
    Streaming transcript snippets as soon as the model emits them.
  - ``error`` ``{"code": str, "message": str}`` Non-fatal errors that
    don't break the connection.

None of these are routed at Jalon 5 — the WS is closed on connect.
"""

from fastapi import APIRouter, WebSocket, status
from pydantic import BaseModel, Field

from app.core.errors import AppError

router = APIRouter(tags=["jdr-live"])


class LiveNotImplementedError(AppError):
    """Documented stub — the live ingestion contract has no implementation yet."""

    status_code = status.HTTP_501_NOT_IMPLEMENTED
    error_type = "live-not-implemented"
    title = "Live mode not implemented"


class LiveSessionInit(BaseModel):
    """Body that the future ``POST /live/sessions`` will accept.

    Published as a Pydantic model so the OpenAPI schema is complete even
    though the handler always raises 501.
    """

    title: str = Field(
        ..., min_length=1, max_length=500,
        description="Human-readable title for the live session.",
    )
    campaign_context: str | None = Field(
        default=None,
        max_length=8000,
        description="Optional campaign-bible block (same semantics as batch mode).",
    )


@router.post(
    "/live/sessions",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    summary="(stub — Jalon 5) Open a live ingestion session.",
)
async def post_live_session(payload: LiveSessionInit):
    """Always raises 501 ``live-not-implemented``.

    The Pydantic body is validated first so a malformed payload still
    returns 422, keeping the published contract honest even when the
    implementation lands later.
    """
    _ = payload  # accepted, parsed, then discarded
    raise LiveNotImplementedError(
        detail=(
            "The live ingestion endpoint contract is published but no "
            "implementation is delivered at Jalon 5. See "
            "docs/services/jdr.md for the future event schema."
        ),
    )


@router.websocket("/live/stream")
async def live_stream_stub(ws: WebSocket) -> None:
    """Stub WebSocket — accept then close with code 1011.

    Per the contract (rest-api.md §354-356), the WS must close on
    connect with ``1011 Internal Error`` and a reason mentioning the
    Jalon 5 stub. A client that sees this knows the surface exists but
    the implementation doesn't.
    """
    await ws.accept()
    await ws.close(code=1011, reason="stub — not yet implemented at Jalon 5")
