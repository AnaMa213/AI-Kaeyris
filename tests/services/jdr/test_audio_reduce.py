"""BD-9 - server-side audio reduce helper."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.jdr.audio import (
    AudioReduceError,
    prepare_audio_for_transcription,
)


def test_prepare_audio_for_transcription_creates_reduced_file(tmp_path: Path):
    source = tmp_path / "raw.m4a"
    target = tmp_path / "audios" / "prepared.m4a"
    source.write_bytes(b"raw-audio")

    def fake_run(cmd, **kwargs):
        target.write_bytes(b"reduced-audio")
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="",
            stderr="",
        )

    with patch("app.services.jdr.audio.subprocess.run", side_effect=fake_run) as run:
        result = prepare_audio_for_transcription(source, target)

    assert result.path == target
    assert result.size_bytes == len(b"reduced-audio")
    assert len(result.sha256) == 64
    assert target.read_bytes() == b"reduced-audio"
    cmd = run.call_args.args[0]
    assert cmd[0] == "ffmpeg"
    assert "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "aac"
    assert "-b:a" in cmd and cmd[cmd.index("-b:a") + 1] == "24k"
    assert "-ac" in cmd and cmd[cmd.index("-ac") + 1] == "1"
    assert cmd[-1] == str(target)


def test_prepare_audio_for_transcription_raises_when_ffmpeg_missing(
    tmp_path: Path,
):
    source = tmp_path / "raw.m4a"
    target = tmp_path / "prepared.m4a"
    source.write_bytes(b"raw-audio")

    with patch(
        "app.services.jdr.audio.subprocess.run",
        side_effect=FileNotFoundError(2, "no such file", "ffmpeg"),
    ):
        with pytest.raises(AudioReduceError, match="ffmpeg binary not found"):
            prepare_audio_for_transcription(source, target)


def test_prepare_audio_for_transcription_raises_on_nonzero_exit(
    tmp_path: Path,
):
    source = tmp_path / "raw.m4a"
    target = tmp_path / "prepared.m4a"
    source.write_bytes(b"raw-audio")

    def fake_run(cmd, **kwargs):
        target.write_bytes(b"partial")
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=1,
            stdout="",
            stderr="bad audio",
        )

    with patch("app.services.jdr.audio.subprocess.run", side_effect=fake_run):
        with pytest.raises(AudioReduceError, match="exited with code 1"):
            prepare_audio_for_transcription(source, target)

    assert not target.exists()


def test_prepare_audio_for_transcription_raises_on_empty_output(
    tmp_path: Path,
):
    source = tmp_path / "raw.m4a"
    target = tmp_path / "prepared.m4a"
    source.write_bytes(b"raw-audio")

    def fake_run(cmd, **kwargs):
        target.write_bytes(b"")
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="",
            stderr="",
        )

    with patch("app.services.jdr.audio.subprocess.run", side_effect=fake_run):
        with pytest.raises(AudioReduceError, match="empty output"):
            prepare_audio_for_transcription(source, target)

    assert not target.exists()
