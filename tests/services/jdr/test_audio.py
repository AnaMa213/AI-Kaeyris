"""Tests for ``app/services/jdr/audio.py``.

The real ffmpeg invocation is mocked out — we verify the command we send
and the way we map ffmpeg output back into ``(offset, path)`` tuples.
A separate integration test (skipped when ffmpeg is unavailable) exercises
the real binary on a synthetic sine wave to catch CLI-arg regressions.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.jdr.audio import AudioChunkingError, chunked_audio


def _fake_run_creating_chunks(
    work_dir: Path, count: int
) -> subprocess.CompletedProcess:
    """Return a fake CompletedProcess and pre-create the chunk files."""
    for i in range(count):
        (work_dir / f"chunk_{i:05d}.wav").write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def test_chunked_audio_returns_indexed_paths(tmp_path: Path):
    source = tmp_path / "in.m4a"
    source.write_bytes(b"unused-by-the-mock")
    work_dir = tmp_path / "chunks"

    def fake_run(*args, **kwargs):
        return _fake_run_creating_chunks(work_dir, 3)

    with patch("app.services.jdr.audio.subprocess.run", side_effect=fake_run):
        with chunked_audio(source, chunk_duration_seconds=30, work_dir=work_dir) as out:
            assert len(out) == 3
            offsets = [offset for offset, _ in out]
            assert offsets == [0.0, 30.0, 60.0]
            # Every chunk path is inside work_dir.
            for _, path in out:
                assert path.parent == work_dir


def test_chunked_audio_cleans_up_work_dir_on_exit(tmp_path: Path):
    source = tmp_path / "in.m4a"
    source.write_bytes(b"x")
    work_dir = tmp_path / "chunks"

    def fake_run(*args, **kwargs):
        return _fake_run_creating_chunks(work_dir, 2)

    with patch("app.services.jdr.audio.subprocess.run", side_effect=fake_run):
        with chunked_audio(source, chunk_duration_seconds=15, work_dir=work_dir):
            assert work_dir.exists()

    assert not work_dir.exists()


def test_chunked_audio_cleans_up_when_caller_raises(tmp_path: Path):
    source = tmp_path / "in.m4a"
    source.write_bytes(b"x")
    work_dir = tmp_path / "chunks"

    def fake_run(*args, **kwargs):
        return _fake_run_creating_chunks(work_dir, 1)

    with patch("app.services.jdr.audio.subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="boom"):
            with chunked_audio(source, 15, work_dir):
                raise RuntimeError("boom")

    assert not work_dir.exists()


def test_chunked_audio_raises_when_ffmpeg_missing(tmp_path: Path):
    source = tmp_path / "in.m4a"
    source.write_bytes(b"x")
    work_dir = tmp_path / "chunks"

    with patch(
        "app.services.jdr.audio.subprocess.run",
        side_effect=FileNotFoundError(2, "no such file", "ffmpeg"),
    ):
        with pytest.raises(AudioChunkingError, match="ffmpeg binary not found"):
            with chunked_audio(source, 30, work_dir):
                pass


def test_chunked_audio_raises_on_nonzero_exit(tmp_path: Path):
    source = tmp_path / "in.m4a"
    source.write_bytes(b"x")
    work_dir = tmp_path / "chunks"

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="some ffmpeg error"
        )

    with patch("app.services.jdr.audio.subprocess.run", side_effect=fake_run):
        with pytest.raises(AudioChunkingError, match="exited with code 1"):
            with chunked_audio(source, 30, work_dir):
                pass


def test_chunked_audio_raises_when_no_chunks_produced(tmp_path: Path):
    source = tmp_path / "in.m4a"
    source.write_bytes(b"x")
    work_dir = tmp_path / "chunks"

    def fake_run(*args, **kwargs):
        # Success exit but no files written — ffmpeg behaviour on truncated
        # or unreadable inputs in some edge cases.
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

    with patch("app.services.jdr.audio.subprocess.run", side_effect=fake_run):
        with pytest.raises(AudioChunkingError, match="no chunks"):
            with chunked_audio(source, 30, work_dir):
                pass


def test_chunked_audio_passes_correct_ffmpeg_cli(tmp_path: Path):
    """The ffmpeg arg vector is part of the contract — re-encoding mono
    16 kHz WAV is what Whisper expects internally, and segment muxing with
    ``-reset_timestamps 1`` is what makes per-chunk offsets meaningful."""
    source = tmp_path / "in.m4a"
    source.write_bytes(b"x")
    work_dir = tmp_path / "chunks"
    captured: dict[str, list[str]] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return _fake_run_creating_chunks(work_dir, 1)

    with patch("app.services.jdr.audio.subprocess.run", side_effect=fake_run):
        with chunked_audio(source, 30, work_dir):
            pass

    cmd = captured["cmd"]
    assert cmd[0] == "ffmpeg"
    assert "-ac" in cmd and cmd[cmd.index("-ac") + 1] == "1"
    assert "-ar" in cmd and cmd[cmd.index("-ar") + 1] == "16000"
    assert "-f" in cmd and cmd[cmd.index("-f") + 1] == "segment"
    assert "-segment_time" in cmd and cmd[cmd.index("-segment_time") + 1] == "30"
    assert "-reset_timestamps" in cmd and cmd[cmd.index("-reset_timestamps") + 1] == "1"
    assert cmd[-1].endswith("chunk_%05d.wav")


# ---------------------------------------------------------------------------
# Optional integration test — only runs when real ffmpeg is on PATH.
# Keeps the CI hermetic while still catching CLI regressions locally.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not available on this host"
)
def test_chunked_audio_integration_with_real_ffmpeg(tmp_path: Path):
    """Generate a 75s synthetic sine wave and chunk it into ~30s pieces."""
    source = tmp_path / "sine.wav"
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=75",
            "-ar", "16000", "-ac", "1",
            str(source),
        ],
        check=True,
    )
    work_dir = tmp_path / "chunks"
    with chunked_audio(source, chunk_duration_seconds=30, work_dir=work_dir) as out:
        # 75s / 30s -> 3 chunks (the last one being 15s).
        assert len(out) == 3
        offsets = [offset for offset, _ in out]
        assert offsets == [0.0, 30.0, 60.0]
        for _, path in out:
            assert path.exists()
            assert path.stat().st_size > 0
