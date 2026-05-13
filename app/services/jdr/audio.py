"""Audio-level helpers for the JDR pipeline.

Currently a single primitive: split an uploaded audio file into
fixed-duration chunks using ffmpeg's ``segment`` muxer. The transcription
job calls this before invoking the Whisper adapter so that a repetition
loop on one chunk cannot poison the rest of the session.

Re-encoded to mono 16 kHz WAV — that's the canonical input shape Whisper
converts to internally anyway, and unlike stream-copy on m4a/mp4
containers it does not depend on the moov atom being at the front of the
file. The cost (~1 MB per 30s chunk on disk) is bounded by the per-chunk
cleanup the caller is responsible for.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


class AudioChunkingError(RuntimeError):
    """Raised when ffmpeg fails or produces no output."""


@contextmanager
def chunked_audio(
    source: Path,
    chunk_duration_seconds: int,
    work_dir: Path,
) -> Iterator[list[tuple[float, Path]]]:
    """Yield ``[(start_offset_seconds, chunk_path), …]`` for *source*.

    The temp directory under ``work_dir`` is created on entry and removed
    on exit (success *or* failure) — callers don't have to clean up.

    Raises :class:`AudioChunkingError` if ffmpeg is missing, fails, or
    produces no chunk file. The caller can decide whether that is fatal
    (current job logic) or whether to fall back to single-shot.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-loglevel", "error",
        "-y",
        "-i", str(source),
        "-ac", "1",
        "-ar", "16000",
        "-f", "segment",
        "-segment_time", str(chunk_duration_seconds),
        "-reset_timestamps", "1",
        str(work_dir / "chunk_%05d.wav"),
    ]
    try:
        result = subprocess.run(
            cmd, check=False, capture_output=True, text=True, timeout=600
        )
    except FileNotFoundError as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise AudioChunkingError(
            "ffmpeg binary not found on PATH. Install ffmpeg in the worker "
            "image (apt-get install ffmpeg)."
        ) from exc
    except subprocess.SubprocessError as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise AudioChunkingError(f"ffmpeg invocation failed: {exc}") from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        shutil.rmtree(work_dir, ignore_errors=True)
        raise AudioChunkingError(
            f"ffmpeg exited with code {result.returncode}: {stderr}"
        )

    chunks = sorted(work_dir.glob("chunk_*.wav"))
    if not chunks:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise AudioChunkingError(
            f"ffmpeg produced no chunks for {source}."
        )

    indexed = [
        (float(idx * chunk_duration_seconds), chunk)
        for idx, chunk in enumerate(chunks)
    ]
    logger.info(
        "audio.chunked",
        extra={
            "source": str(source),
            "chunk_count": len(indexed),
            "chunk_duration_seconds": chunk_duration_seconds,
        },
    )
    try:
        yield indexed
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
