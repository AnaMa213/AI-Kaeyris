"""Local model validation probes for BD-20.

Runtime imports are deliberately lazy: most deployments keep using cloud or
HTTP-compatible providers and should not need local inference packages.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings


@dataclass(frozen=True, slots=True)
class LocalModelProbeResult:
    runtime: str
    model_format: str
    message: str


class LocalModelProbeError(Exception):
    """Safe, frontend-facing validation failure."""

    def __init__(self, problem_type: str, title: str, detail: str) -> None:
        super().__init__(detail)
        self.problem_type = problem_type
        self.title = title
        self.detail = detail


def normalize_model_path(model_path: str) -> str:
    stripped = model_path.strip()
    if not stripped:
        raise LocalModelProbeError(
            "local-model-path-not-found",
            "Local model path is required",
            "Provide a local model path before validation.",
        )
    return str(Path(stripped).expanduser().resolve(strict=False))


async def probe_local_model(
    *,
    category: str,
    model_path: str,
    timeout_seconds: float | None = None,
) -> LocalModelProbeResult:
    normalized = normalize_model_path(model_path)
    timeout = timeout_seconds or settings.LOCAL_MODEL_VALIDATION_TIMEOUT_SECONDS
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_probe_local_model_sync, category, Path(normalized)),
            timeout=timeout,
        )
    except TimeoutError as exc:
        raise LocalModelProbeError(
            "local-model-timeout",
            "Local model validation timed out",
            "The local model did not load within the configured validation budget.",
        ) from exc


def _probe_local_model_sync(category: str, path: Path) -> LocalModelProbeResult:
    _ensure_path_readable(path)
    if category == "transcription":
        return _probe_transcription_model(path)
    if category == "summary":
        return _probe_summary_model(path)
    raise LocalModelProbeError(
        "local-model-incompatible-task",
        "Unsupported local model category",
        "Local model validation supports transcription and summary only.",
    )


def _ensure_path_readable(path: Path) -> None:
    if not path.exists():
        raise LocalModelProbeError(
            "local-model-path-not-found",
            "Local model path not found",
            "The local model path does not exist from the backend process.",
        )
    if not os.access(path, os.R_OK):
        raise LocalModelProbeError(
            "local-model-path-not-found",
            "Local model path is not readable",
            "The backend process cannot read the local model path.",
        )


def _probe_transcription_model(path: Path) -> LocalModelProbeResult:
    if not path.is_dir() or not (path / "model.bin").exists():
        raise LocalModelProbeError(
            "local-model-unsupported-format",
            "Unsupported transcription model format",
            "Transcription Local mode expects a readable CTranslate2 Whisper directory.",
        )
    try:
        from faster_whisper import WhisperModel

        WhisperModel(
            str(path),
            device=settings.LOCAL_MODEL_DEVICE,
            compute_type=settings.LOCAL_WHISPER_COMPUTE_TYPE,
        )
    except ModuleNotFoundError as exc:
        raise LocalModelProbeError(
            "local-model-unsupported-format",
            "Local transcription runtime unavailable",
            "The backend environment does not have the local transcription runtime installed.",
        ) from exc
    except MemoryError as exc:
        raise LocalModelProbeError(
            "local-model-out-of-memory",
            "Local transcription model exceeded memory",
            "The local transcription model could not be loaded within available memory.",
        ) from exc
    except Exception as exc:
        raise LocalModelProbeError(
            "local-model-incompatible-task",
            "Local transcription model is not compatible",
            "The model could not be loaded as a transcription model.",
        ) from exc

    return LocalModelProbeResult(
        runtime="faster-whisper",
        model_format="ctranslate2-whisper",
        message="Model loaded and accepted for transcription.",
    )


def _probe_summary_model(path: Path) -> LocalModelProbeResult:
    if not path.is_file() or path.suffix.lower() != ".gguf":
        raise LocalModelProbeError(
            "local-model-unsupported-format",
            "Unsupported summary model format",
            "Summary Local mode expects a readable GGUF model file.",
        )
    try:
        from llama_cpp import Llama

        model = Llama(
            model_path=str(path),
            n_ctx=settings.LOCAL_LLM_CONTEXT_TOKENS,
            n_gpu_layers=settings.LOCAL_LLM_GPU_LAYERS,
            verbose=False,
        )
        model("Validation probe.", max_tokens=1)
    except ModuleNotFoundError as exc:
        raise LocalModelProbeError(
            "local-model-unsupported-format",
            "Local summary runtime unavailable",
            "The backend environment does not have the local summary runtime installed.",
        ) from exc
    except MemoryError as exc:
        raise LocalModelProbeError(
            "local-model-out-of-memory",
            "Local summary model exceeded memory",
            "The local summary model could not be loaded within available memory.",
        ) from exc
    except Exception as exc:
        raise LocalModelProbeError(
            "local-model-incompatible-task",
            "Local summary model is not compatible",
            "The model could not be loaded as a summary/text-generation model.",
        ) from exc

    return LocalModelProbeResult(
        runtime="llama-cpp-python",
        model_format="gguf",
        message="Model loaded and accepted for summary.",
    )
