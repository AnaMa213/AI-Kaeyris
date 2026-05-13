"""Transcription adapter — vendor-neutral interface and OpenAI-compatible impl.

ADR 0006 §2 + spec ``contracts/transcription-adapter.md``. Mirrors the
LLMAdapter (jalon 4) pattern: a single ``OpenAICompatibleTranscriptionAdapter``
covers both postures by parameterising ``base_url`` — cloud endpoints
(OpenAI Whisper API and compatibles) and a self-hosted LAN GPU host
running faster-whisper + pyannote behind an OpenAI-compatible facade.

The cloud endpoint does not produce diarisation: every segment lands with
``speaker_label='unknown'``. The local endpoint enriches each segment
with a ``speaker`` field that the adapter maps to ``speaker_label``.

Chunking of files > 25 MB (the cloud limit) is handled at job level
(``app/jobs/jdr.py``), not in this adapter.
"""

import logging
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)

from app.core.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TranscriptionError(Exception):
    """Base class for transcription adapter errors."""


class TransientTranscriptionError(TranscriptionError):
    """Retryable: 5xx upstream, timeout, connection error, rate-limit (429)."""


class PermanentTranscriptionError(TranscriptionError):
    """Non-retryable: 4xx (excl. 429), audio invalide, auth invalide."""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TranscriptionSegment:
    """One diarised utterance.

    ``speaker_label`` is the raw label produced by the backend
    (``speaker_1``, ``speaker_2``, ``unknown``…). The mapping
    ``speaker_label -> pj_id`` is a per-session decision made later in
    business logic, not by the adapter.
    """

    speaker_label: str
    start_seconds: float
    end_seconds: float
    text: str


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    """Full transcription of an audio file."""

    segments: list[TranscriptionSegment]
    language: str  # BCP-47, e.g. "fr"
    model_used: str  # e.g. "openai:whisper-large-v3"
    provider: str  # "cloud" | "local"


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


class TranscriptionAdapter(Protocol):
    """Vendor-neutral speech-to-text contract.

    A single operation by design: each consumer (job, test) supplies the
    audio path and an optional language hint. Diarisation, chunking and
    model choice are internal to the implementation.
    """

    async def transcribe(
        self,
        *,
        audio_path: str,
        language_hint: str | None = None,
    ) -> TranscriptionResult: ...


# ---------------------------------------------------------------------------
# OpenAI-compatible implementation
# ---------------------------------------------------------------------------

# Default base URLs for known providers. None = use the SDK default
# (api.openai.com). Local deployments must set TRANSCRIPTION_BASE_URL
# explicitly because there is no "default LAN GPU host" address.
_DEFAULT_BASE_URLS: dict[str, str | None] = {
    "cloud": None,  # SDK default = OpenAI Whisper API
    "local": None,  # operator must set TRANSCRIPTION_BASE_URL
}


class OpenAICompatibleTranscriptionAdapter:
    """Single implementation for every OpenAI-compatible transcription endpoint.

    Instantiate via ``build_transcription_adapter`` (factory below) which
    wires the right defaults from ``settings``.
    """

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        api_key: str,
        base_url: str | None = None,
        timeout_seconds: float = 1800.0,
    ) -> None:
        self.provider = provider
        self.model = model
        client_kwargs: dict[str, object] = {
            "api_key": api_key,
            "timeout": timeout_seconds,
        }
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**client_kwargs)

    async def transcribe(
        self,
        *,
        audio_path: str,
        language_hint: str | None = None,
    ) -> TranscriptionResult:
        start = time.time()
        try:
            with open(audio_path, "rb") as audio_file:
                # response_format="verbose_json" gives us the segments + duration.
                # The local LAN endpoint enriches each segment with a "speaker"
                # field; the cloud OpenAI Whisper API does not.
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "file": audio_file,
                    "response_format": "verbose_json",
                    # temperature=0 -> deterministic decoding, fewer hallucinations
                    "temperature": 0,
                }
                if language_hint:
                    kwargs["language"] = language_hint

                # speaches/faster-whisper-specific options to fight repetition
                # loops on long sessions (Whisper's known failure mode on silence
                # or background noise). Passed via extra_body so the openai SDK
                # forwards them as additional multipart fields. The cloud OpenAI
                # Whisper API silently ignores unknown fields, so this is safe
                # for both providers.
                kwargs["extra_body"] = {
                    "vad_filter": True,
                    "condition_on_previous_text": False,
                }

                resp = await self._client.audio.transcriptions.create(**kwargs)
        except (
            APITimeoutError,
            APIConnectionError,
            RateLimitError,
            InternalServerError,
        ) as exc:
            raise TransientTranscriptionError(
                f"{type(exc).__name__}: {exc}"
            ) from exc
        except (
            AuthenticationError,
            PermissionDeniedError,
            BadRequestError,
            NotFoundError,
            UnprocessableEntityError,
        ) as exc:
            raise PermanentTranscriptionError(
                f"{type(exc).__name__}: {exc}"
            ) from exc
        except APIStatusError as exc:
            if 500 <= exc.status_code < 600:
                raise TransientTranscriptionError(
                    f"HTTP {exc.status_code}: {exc}"
                ) from exc
            raise PermanentTranscriptionError(
                f"HTTP {exc.status_code}: {exc}"
            ) from exc
        except APIError as exc:
            raise PermanentTranscriptionError(
                f"{type(exc).__name__}: {exc}"
            ) from exc
        except OSError as exc:
            # Audio file not found / unreadable — definitive failure.
            raise PermanentTranscriptionError(
                f"Cannot read audio file {audio_path!r}: {exc}"
            ) from exc

        duration_ms = int((time.time() - start) * 1000)
        segments = _extract_segments(resp)
        result = TranscriptionResult(
            segments=segments,
            language=getattr(resp, "language", "") or (language_hint or ""),
            model_used=f"{self.provider}:{self.model}",
            provider=self.provider,
        )
        logger.info(
            "transcription.complete",
            extra={
                "provider": self.provider,
                "model": self.model,
                "audio_path": audio_path,
                "segments_count": len(segments),
                "duration_ms": duration_ms,
            },
        )
        return result


def _extract_segments(resp: Any) -> list[TranscriptionSegment]:
    """Map an OpenAI-compatible verbose response to our domain segments.

    The cloud OpenAI Whisper API returns segments without a ``speaker``
    field — every segment falls back to ``speaker_label='unknown'``.
    The local LAN endpoint adds a ``speaker`` field that we surface
    directly as the segment label.
    """
    raw_segments = getattr(resp, "segments", None) or []
    out: list[TranscriptionSegment] = []
    for seg in raw_segments:
        # `seg` may be a Pydantic model (cloud) or a dict (local custom).
        get = (
            (lambda key, default=None: getattr(seg, key, default))
            if not isinstance(seg, dict)
            else seg.get
        )
        speaker = get("speaker") or "unknown"
        out.append(
            TranscriptionSegment(
                speaker_label=str(speaker),
                start_seconds=float(get("start", 0.0) or 0.0),
                end_seconds=float(get("end", 0.0) or 0.0),
                text=str(get("text", "") or "").strip(),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Mock implementation (tests)
# ---------------------------------------------------------------------------


class MockTranscriptionAdapter:
    """Deterministic, instant, no-network adapter for unit tests.

    Returns three fixed segments regardless of the input file. The exact
    output is part of the public contract — tests assert against it.
    """

    async def transcribe(
        self,
        *,
        audio_path: str,
        language_hint: str | None = None,
    ) -> TranscriptionResult:
        return TranscriptionResult(
            segments=[
                TranscriptionSegment("speaker_1", 0.0, 1.5, "[mock] segment one"),
                TranscriptionSegment("speaker_2", 1.5, 3.0, "[mock] segment two"),
                TranscriptionSegment("speaker_1", 3.0, 4.5, "[mock] segment three"),
            ],
            language=language_hint or "fr",
            model_used="mock:whisper",
            provider="mock",
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_transcription_adapter() -> TranscriptionAdapter:
    """Build an adapter from current settings. Raises if config is invalid."""
    provider = settings.TRANSCRIPTION_PROVIDER.strip().lower()
    if provider == "mock":
        return MockTranscriptionAdapter()
    if provider not in _DEFAULT_BASE_URLS:
        raise RuntimeError(
            f"Unknown TRANSCRIPTION_PROVIDER {provider!r}. "
            f"Supported: {sorted({*_DEFAULT_BASE_URLS, 'mock'})}"
        )
    if provider == "local" and not settings.TRANSCRIPTION_BASE_URL:
        raise RuntimeError(
            "TRANSCRIPTION_PROVIDER='local' requires TRANSCRIPTION_BASE_URL "
            "(e.g. http://gpu-host.lan:8001/v1)."
        )
    if provider == "cloud" and not settings.TRANSCRIPTION_API_KEY:
        raise RuntimeError(
            "TRANSCRIPTION_PROVIDER='cloud' requires TRANSCRIPTION_API_KEY. "
            "Set TRANSCRIPTION_PROVIDER=mock for tests."
        )

    base_url = settings.TRANSCRIPTION_BASE_URL or _DEFAULT_BASE_URLS[provider]
    return OpenAICompatibleTranscriptionAdapter(
        provider=provider,
        model=settings.TRANSCRIPTION_MODEL,
        api_key=settings.TRANSCRIPTION_API_KEY or "noop",
        base_url=base_url,
        timeout_seconds=settings.TRANSCRIPTION_TIMEOUT_SECONDS,
    )


@lru_cache(maxsize=1)
def get_transcription_adapter() -> TranscriptionAdapter:
    """FastAPI / job dependency: process-wide adapter (memoised)."""
    return build_transcription_adapter()
