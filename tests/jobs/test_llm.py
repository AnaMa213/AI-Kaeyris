"""Tests the llm_complete job's adapter wiring and error mapping."""

import pytest

from app.adapters.llm import (
    MockLLMAdapter,
    PermanentLLMError,
    TransientLLMError,
    get_llm_adapter,
)
from app.jobs import PermanentJobError, TransientJobError
from app.jobs.llm import llm_complete


@pytest.fixture(autouse=True)
def _clear_cache():
    get_llm_adapter.cache_clear()
    yield
    get_llm_adapter.cache_clear()


def test_llm_complete_uses_mock_adapter(monkeypatch):
    monkeypatch.setattr("app.adapters.llm.settings.LLM_PROVIDER", "mock")
    out = llm_complete(system="be terse", user="hello", max_tokens=10)
    assert "hello" in out


class _RaisingAdapter:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        raise self._exc


def test_transient_llm_error_becomes_transient_job_error(monkeypatch):
    monkeypatch.setattr(
        "app.jobs.llm.get_llm_adapter",
        lambda: _RaisingAdapter(TransientLLMError("upstream 503")),
    )
    with pytest.raises(TransientJobError, match="upstream 503"):
        llm_complete(system="s", user="u", max_tokens=10)


def test_permanent_llm_error_becomes_permanent_job_error(monkeypatch):
    monkeypatch.setattr(
        "app.jobs.llm.get_llm_adapter",
        lambda: _RaisingAdapter(PermanentLLMError("invalid prompt")),
    )
    with pytest.raises(PermanentJobError, match="invalid prompt"):
        llm_complete(system="s", user="u", max_tokens=10)


def test_mock_adapter_directly_via_protocol():
    adapter: MockLLMAdapter = MockLLMAdapter()
    # Confirm the structural shape — anything callable async with this
    # signature satisfies LLMAdapter.
    import asyncio
    out = asyncio.run(adapter.complete(system="x", user="y", max_tokens=1))
    assert "x" in out and "y" in out
