"""Tests for the LLMAdapter Protocol, factory and error mapping."""

from typing import cast
from unittest.mock import AsyncMock

import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)

from app.adapters.llm import (
    LLMAdapter,
    MockLLMAdapter,
    OpenAICompatibleLLMAdapter,
    PermanentLLMError,
    TransientLLMError,
    build_llm_adapter,
    get_llm_adapter,
)


# ---- Mock adapter ---------------------------------------------------------


async def test_mock_adapter_returns_deterministic_string():
    adapter: LLMAdapter = MockLLMAdapter()
    out = await adapter.complete(system="be terse", user="hello world", max_tokens=10)
    assert "hello world"[:30] in out
    assert "be terse" in out


# ---- Factory --------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_adapter_cache():
    get_llm_adapter.cache_clear()
    yield
    get_llm_adapter.cache_clear()


def test_build_factory_returns_mock_for_mock_provider(monkeypatch):
    monkeypatch.setattr("app.adapters.llm.settings.LLM_PROVIDER", "mock")
    adapter = build_llm_adapter()
    assert isinstance(adapter, MockLLMAdapter)


def test_build_factory_rejects_unknown_provider(monkeypatch):
    monkeypatch.setattr("app.adapters.llm.settings.LLM_PROVIDER", "wat")
    with pytest.raises(RuntimeError, match="Unknown LLM_PROVIDER"):
        build_llm_adapter()


def test_build_factory_requires_api_key_for_cloud(monkeypatch):
    monkeypatch.setattr("app.adapters.llm.settings.LLM_PROVIDER", "deepinfra")
    monkeypatch.setattr("app.adapters.llm.settings.LLM_API_KEY", "")
    with pytest.raises(RuntimeError, match="LLM_API_KEY is required"):
        build_llm_adapter()


def test_build_factory_allows_empty_key_for_local(monkeypatch):
    monkeypatch.setattr("app.adapters.llm.settings.LLM_PROVIDER", "ollama")
    monkeypatch.setattr("app.adapters.llm.settings.LLM_API_KEY", "")
    adapter = build_llm_adapter()
    assert isinstance(adapter, OpenAICompatibleLLMAdapter)
    assert adapter.provider == "ollama"


def test_get_llm_adapter_caches(monkeypatch):
    monkeypatch.setattr("app.adapters.llm.settings.LLM_PROVIDER", "mock")
    a = get_llm_adapter()
    b = get_llm_adapter()
    assert a is b


# ---- Error mapping (OpenAICompatibleLLMAdapter) ---------------------------


def _adapter_with_failing_client(exc_to_raise: Exception) -> OpenAICompatibleLLMAdapter:
    """Build an adapter whose underlying SDK raises ``exc_to_raise``."""
    adapter = OpenAICompatibleLLMAdapter(
        provider="deepinfra",
        model="meta-llama/Meta-Llama-3.1-8B-Instruct",
        api_key="dummy",
        base_url="https://api.deepinfra.com/v1/openai",
    )
    failing = AsyncMock(side_effect=exc_to_raise)
    adapter._client.chat.completions.create = failing  # type: ignore[method-assign]
    return adapter


def _make_status_exc(cls, status_code: int) -> Exception:
    """Build an OpenAI status-error instance.

    The SDK's status-error constructors require a Response object; we
    bypass that for tests by creating a bare instance via __new__ and
    setting the public attributes the adapter relies on.
    """
    exc = cls.__new__(cls)
    exc.status_code = status_code
    exc.message = f"synthetic {status_code}"
    exc.body = None
    exc.response = cast(object, None)
    exc.request_id = None
    Exception.__init__(exc, exc.message)
    return exc


@pytest.mark.parametrize(
    "exc",
    [
        APITimeoutError(request=cast(object, None)),  # type: ignore[arg-type]
        APIConnectionError(request=cast(object, None)),  # type: ignore[arg-type]
        _make_status_exc(RateLimitError, 429),
        _make_status_exc(InternalServerError, 500),
    ],
)
async def test_transient_errors_are_remapped(exc):
    adapter = _adapter_with_failing_client(exc)
    with pytest.raises(TransientLLMError):
        await adapter.complete(system="s", user="u", max_tokens=10)


@pytest.mark.parametrize(
    "exc",
    [
        _make_status_exc(AuthenticationError, 401),
        _make_status_exc(PermissionDeniedError, 403),
        _make_status_exc(BadRequestError, 400),
        _make_status_exc(UnprocessableEntityError, 422),
    ],
)
async def test_permanent_errors_are_remapped(exc):
    adapter = _adapter_with_failing_client(exc)
    with pytest.raises(PermanentLLMError):
        await adapter.complete(system="s", user="u", max_tokens=10)
