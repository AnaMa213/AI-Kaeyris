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

import asyncio
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
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
from app.core.logging import get_logger
from app.core.metrics import (
    TRANSCRIPTION_CALLS_TOTAL,
    TRANSCRIPTION_DURATION_SECONDS,
)

logger = get_logger(__name__)


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
_PERSONAL_CLOUD_BASE_URL = "https://api.deepinfra.com/v1/openai"


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
        self.base_url = base_url
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
        outcome = "success"
        try:
            try:
                with open(audio_path, "rb") as audio_file:
                    # response_format="verbose_json" gives us the segments + duration.
                    # The local LAN endpoint enriches each segment with a "speaker"
                    # field; the cloud OpenAI Whisper API does not.
                    kwargs: dict[str, Any] = {
                        "model": self.model,
                        "file": audio_file,
                        "response_format": "verbose_json",
                    }
                    if language_hint:
                        kwargs["language"] = language_hint
                    # WARNING about anti-hallucination knobs (vad_filter,
                    # hallucination_silence_threshold, compression_ratio_threshold,
                    # condition_on_previous_text, log_prob_threshold,
                    # no_speech_threshold, vad_parameters):
                    #
                    # We *cannot* configure them from the client. speaches'
                    # /v1/audio/transcriptions FastAPI route only accepts the
                    # OpenAI-spec fields plus {hotwords, stream, without_timestamps,
                    # timestamp_granularities}. Anything else passed via extra_body
                    # is silently dropped before it reaches faster-whisper. They
                    # must be configured on the server (env vars
                    # _UNSTABLE_VAD_FILTER, WHISPER__*) or worked around at the
                    # model level (large-v3-turbo is much less prone to repetition
                    # loops than large-v3 on long French audio).
                    #
                    # Likewise we do NOT pin temperature=0. faster-whisper's only
                    # remaining defence against degenerate output is its temperature
                    # fallback tuple (0.0, 0.2, 0.4, 0.6, 0.8, 1.0): when a window
                    # comes back with too-high compression ratio or too-low log
                    # prob, it re-decodes at the next temperature. Pinning a scalar
                    # disables that fallback.

                    resp = await self._client.audio.transcriptions.create(**kwargs)
            except (
                APITimeoutError,
                APIConnectionError,
                RateLimitError,
                InternalServerError,
            ) as exc:
                outcome = "transient"
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
                outcome = "permanent"
                raise PermanentTranscriptionError(
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            except APIStatusError as exc:
                if 500 <= exc.status_code < 600:
                    outcome = "transient"
                    raise TransientTranscriptionError(
                        f"HTTP {exc.status_code}: {exc}"
                    ) from exc
                outcome = "permanent"
                raise PermanentTranscriptionError(
                    f"HTTP {exc.status_code}: {exc}"
                ) from exc
            except APIError as exc:
                outcome = "permanent"
                raise PermanentTranscriptionError(
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            except OSError as exc:
                # Audio file not found / unreadable — definitive failure.
                outcome = "permanent"
                raise PermanentTranscriptionError(
                    f"Cannot read audio file {audio_path!r}: {exc}"
                ) from exc
        finally:
            duration = time.time() - start
            TRANSCRIPTION_DURATION_SECONDS.labels(
                provider=self.provider
            ).observe(duration)
            TRANSCRIPTION_CALLS_TOTAL.labels(
                provider=self.provider, outcome=outcome
            ).inc()

        duration_ms = int(duration * 1000)
        segments = _extract_segments(resp)
        result = TranscriptionResult(
            segments=segments,
            language=getattr(resp, "language", "") or (language_hint or ""),
            model_used=f"{self.provider}:{self.model}",
            provider=self.provider,
        )
        logger.info(
            "transcription.complete",
            provider=self.provider,
            model=self.model,
            audio_path=audio_path,
            segments_count=len(segments),
            duration_ms=duration_ms,
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


class LocalFasterWhisperTranscriptionAdapter:
    """In-process local transcription adapter backed by faster-whisper."""

    provider = "local"

    def __init__(self, *, model_path: str) -> None:
        self.model_path = model_path
        self.model = Path(model_path).name or "local-transcription-model"
        self._model = None

    async def transcribe(
        self,
        *,
        audio_path: str,
        language_hint: str | None = None,
    ) -> TranscriptionResult:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._transcribe_sync, audio_path, language_hint),
                timeout=settings.TRANSCRIPTION_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise TransientTranscriptionError(
                "Local transcription timed out."
            ) from exc
        except ModuleNotFoundError as exc:
            raise PermanentTranscriptionError(
                "Local transcription runtime is not installed in this backend environment."
            ) from exc
        except MemoryError as exc:
            raise PermanentTranscriptionError(
                "Local transcription model exceeded available memory."
            ) from exc
        except PermanentTranscriptionError:
            raise
        except Exception as exc:
            raise PermanentTranscriptionError("Local transcription failed.") from exc

    def _transcribe_sync(
        self,
        audio_path: str,
        language_hint: str | None,
    ) -> TranscriptionResult:
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self.model_path,
                device=settings.LOCAL_MODEL_DEVICE,
                compute_type=settings.LOCAL_WHISPER_COMPUTE_TYPE,
            )
        raw_segments, info = self._model.transcribe(
            audio_path,
            language=language_hint,
        )
        segments = [
            TranscriptionSegment(
                speaker_label="unknown",
                start_seconds=float(getattr(segment, "start", 0.0) or 0.0),
                end_seconds=float(getattr(segment, "end", 0.0) or 0.0),
                text=str(getattr(segment, "text", "") or "").strip(),
            )
            for segment in raw_segments
        ]
        return TranscriptionResult(
            segments=segments,
            language=getattr(info, "language", "") or (language_hint or ""),
            model_used=f"local:{self.model}",
            provider=self.provider,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_transcription_adapter(
    *,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> TranscriptionAdapter:
    """Build an adapter, falling back to env config for omitted params."""
    explicit_provider = provider is not None
    provider = (provider or settings.TRANSCRIPTION_PROVIDER).strip().lower()
    if provider == "mock":
        return MockTranscriptionAdapter()
    if provider not in _DEFAULT_BASE_URLS:
        config_name = (
            "transcription provider"
            if explicit_provider
            else "TRANSCRIPTION_PROVIDER"
        )
        raise RuntimeError(
            f"Unknown {config_name} {provider!r}. "
            f"Supported: {sorted({*_DEFAULT_BASE_URLS, 'mock'})}"
        )
    resolved_base_url = (
        base_url.strip()
        if base_url and base_url.strip()
        else settings.TRANSCRIPTION_BASE_URL or _DEFAULT_BASE_URLS[provider]
    )
    resolved_key = (
        api_key if api_key is not None else settings.TRANSCRIPTION_API_KEY
    )
    if provider == "local" and not resolved_base_url:
        raise RuntimeError(
            "TRANSCRIPTION_PROVIDER='local' requires TRANSCRIPTION_BASE_URL "
            "(e.g. http://gpu-host.lan:8001/v1)."
        )
    if provider == "cloud" and not resolved_key:
        raise RuntimeError(
            "TRANSCRIPTION_PROVIDER='cloud' requires TRANSCRIPTION_API_KEY. "
            "Set TRANSCRIPTION_PROVIDER=mock for tests."
        )

    return OpenAICompatibleTranscriptionAdapter(
        provider=provider,
        model=model or settings.TRANSCRIPTION_MODEL,
        api_key=resolved_key or "noop",
        base_url=resolved_base_url,
        timeout_seconds=settings.TRANSCRIPTION_TIMEOUT_SECONDS,
    )


def build_personal_cloud_transcription_adapter(
    *,
    model: str,
    api_key: str,
) -> TranscriptionAdapter:
    """Build the configured personal cloud transcription adapter."""
    return build_transcription_adapter(
        provider="cloud",
        model=model,
        api_key=api_key,
        base_url=_PERSONAL_CLOUD_BASE_URL,
    )


def build_local_transcription_adapter(*, model_path: str) -> TranscriptionAdapter:
    """Build an in-process local transcription adapter for a validated path."""
    return LocalFasterWhisperTranscriptionAdapter(model_path=model_path)


@lru_cache(maxsize=1)
def get_transcription_adapter() -> TranscriptionAdapter:
    """FastAPI / job dependency: process-wide adapter (memoised)."""
    return build_transcription_adapter()
