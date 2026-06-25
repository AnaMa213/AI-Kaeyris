import asyncio

import pytest

from app.adapters.local_models import (
    LocalModelProbeError,
    normalize_model_path,
    probe_local_model,
)


def test_normalize_model_path_rejects_blank():
    with pytest.raises(LocalModelProbeError) as exc_info:
        normalize_model_path("   ")

    assert exc_info.value.problem_type == "local-model-path-not-found"


async def test_probe_rejects_missing_path(tmp_path):
    missing = tmp_path / "missing-model"

    with pytest.raises(LocalModelProbeError) as exc_info:
        await probe_local_model(category="summary", model_path=str(missing))

    assert exc_info.value.problem_type == "local-model-path-not-found"


async def test_probe_rejects_summary_non_gguf(tmp_path):
    model_file = tmp_path / "model.bin"
    model_file.write_bytes(b"not gguf")

    with pytest.raises(LocalModelProbeError) as exc_info:
        await probe_local_model(category="summary", model_path=str(model_file))

    assert exc_info.value.problem_type == "local-model-unsupported-format"


async def test_probe_timeout_is_user_safe(tmp_path, monkeypatch):
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"fake")

    def slow_probe(category, path):
        _ = (category, path)
        import time

        time.sleep(0.05)

    monkeypatch.setattr("app.adapters.local_models._probe_local_model_sync", slow_probe)

    with pytest.raises(LocalModelProbeError) as exc_info:
        await probe_local_model(
            category="summary",
            model_path=str(model_file),
            timeout_seconds=0.001,
        )

    assert exc_info.value.problem_type == "local-model-timeout"

    await asyncio.sleep(0)
