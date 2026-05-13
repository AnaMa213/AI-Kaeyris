"""Tests the chunked transcription pipeline in ``app/jobs/jdr.py``.

The unit under test is ``_transcribe_with_optional_chunking`` — it owns
the loop "chunk -> adapter.transcribe -> shift timestamps -> merge".
We patch out ``chunked_audio`` so no real ffmpeg is needed, and feed a
fake adapter that returns canned segments per chunk.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import cast
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.adapters.transcription import (
    TranscriptionAdapter,
    TranscriptionResult,
    TranscriptionSegment,
)
from app.jobs.jdr import _transcribe_with_optional_chunking


class _ChunkAwareAdapter:
    """Adapter that returns different segments depending on the chunk index.

    Each chunk is named ``chunk_<idx>.wav``; we use that to pick a fixed
    transcription for each, so the test can verify the merge ordering.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def transcribe(
        self, *, audio_path: str, language_hint: str | None = None
    ) -> TranscriptionResult:
        self.calls.append(audio_path)
        idx = len(self.calls) - 1
        return TranscriptionResult(
            segments=[
                # Each chunk yields segments at "local time" [0, 5] and [5, 10]
                # so the test can verify they get shifted by the chunk offset.
                TranscriptionSegment("speaker_1", 0.0, 5.0, f"hello {idx}"),
                TranscriptionSegment("speaker_1", 5.0, 10.0, f"world {idx}"),
            ],
            language=language_hint or "fr",
            model_used="fake:whisper",
            provider="fake",
        )


@contextmanager
def _fake_chunked_audio(_source, _chunk_duration_seconds, work_dir):
    """Stand-in for ``chunked_audio`` that yields three pre-offset paths."""
    work_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(3):
        path = work_dir / f"chunk_{i:05d}.wav"
        path.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
        paths.append(path)
    yield [(float(i * 30), p) for i, p in enumerate(paths)]


async def test_chunking_shifts_timestamps_and_concatenates(
    tmp_path: Path, monkeypatch
):
    """Each chunk's local timestamps must be offset by its chunk start."""
    monkeypatch.setattr(
        "app.jobs.jdr.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    adapter = _ChunkAwareAdapter()
    with patch("app.jobs.jdr.chunked_audio", _fake_chunked_audio):
        result = await _transcribe_with_optional_chunking(
            adapter=cast(TranscriptionAdapter, adapter),
            audio_path=tmp_path / "session.m4a",
            session_id=uuid4(),
            language_hint="fr",
            chunk_duration_seconds=30,
        )

    # 3 chunks * 2 segments/chunk = 6 segments total
    assert len(result.segments) == 6
    # Chunk 0: offsets 0 -> [0,5] and [5,10]
    assert result.segments[0].start_seconds == 0.0
    assert result.segments[0].end_seconds == 5.0
    assert result.segments[1].end_seconds == 10.0
    # Chunk 1: offsets +30 -> [30,35] and [35,40]
    assert result.segments[2].start_seconds == 30.0
    assert result.segments[2].end_seconds == 35.0
    assert result.segments[3].end_seconds == 40.0
    # Chunk 2: offsets +60 -> [60,65] and [65,70]
    assert result.segments[4].start_seconds == 60.0
    assert result.segments[5].end_seconds == 70.0

    # Each chunk was sent to the adapter exactly once, in order.
    assert len(adapter.calls) == 3
    assert all("chunk_" in c for c in adapter.calls)
    # Metadata is preserved.
    assert result.provider == "fake"
    assert result.language == "fr"


async def test_chunking_disabled_calls_adapter_once_with_full_file(
    tmp_path: Path, monkeypatch
):
    """``chunk_duration_seconds <= 0`` skips ffmpeg entirely."""
    adapter = _ChunkAwareAdapter()
    full_path = tmp_path / "session.m4a"

    # Sentinel: if chunked_audio is touched we want the test to scream.
    @contextmanager
    def _explode(*args, **kwargs):
        raise AssertionError("chunked_audio should not be invoked when disabled")
        yield  # pragma: no cover

    with patch("app.jobs.jdr.chunked_audio", _explode):
        result = await _transcribe_with_optional_chunking(
            adapter=cast(TranscriptionAdapter, adapter),
            audio_path=full_path,
            session_id=uuid4(),
            language_hint="fr",
            chunk_duration_seconds=0,
        )

    assert len(adapter.calls) == 1
    assert adapter.calls[0] == str(full_path)
    # Returned as-is (no offset arithmetic when not chunking).
    assert result.segments[0].start_seconds == 0.0
    assert result.segments[1].end_seconds == 10.0


async def test_chunking_propagates_adapter_error(tmp_path: Path, monkeypatch):
    """A PermanentTranscriptionError on chunk N is surfaced to the caller."""
    from app.adapters.transcription import PermanentTranscriptionError

    monkeypatch.setattr(
        "app.jobs.jdr.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )

    class _FailingOnSecondChunk:
        def __init__(self) -> None:
            self.calls = 0

        async def transcribe(self, *, audio_path, language_hint=None):
            self.calls += 1
            if self.calls == 2:
                raise PermanentTranscriptionError("chunk 1 is bad audio")
            return TranscriptionResult(
                segments=[TranscriptionSegment("speaker_1", 0.0, 1.0, "ok")],
                language="fr",
                model_used="fake:whisper",
                provider="fake",
            )

    adapter = _FailingOnSecondChunk()
    with patch("app.jobs.jdr.chunked_audio", _fake_chunked_audio):
        with pytest.raises(PermanentTranscriptionError, match="chunk 1 is bad"):
            await _transcribe_with_optional_chunking(
                adapter=cast(TranscriptionAdapter, adapter),
                audio_path=tmp_path / "session.m4a",
                session_id=uuid4(),
                language_hint="fr",
                chunk_duration_seconds=30,
            )

    # The second call failed, so we stopped before chunk 2.
    assert adapter.calls == 2
