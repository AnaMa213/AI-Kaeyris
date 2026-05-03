"""LLM adapter — vendor-neutral interface and OpenAI-compatible implementation.

ADR 0005. The interface (Protocol) hides the provider; one concrete class
covers all OpenAI-compatible providers (DeepInfra, Ollama, vLLM, Groq,
Together AI, OpenAI direct) by parameterising base_url + api_key + model.
"""

import logging
import time
from functools import lru_cache
from typing import Protocol

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


class LLMError(Exception):
    """Base class for LLM adapter errors."""


class TransientLLMError(LLMError):
    """Retryable: 5xx upstream, timeout, connection error, rate-limit (429)."""


class PermanentLLMError(LLMError):
    """Non-retryable: 4xx (excl. 429), prompt invalide, auth invalide."""


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


class LLMAdapter(Protocol):
    """Vendor-neutral text LLM contract.

    A single operation by design (ADR 0005 §2): each service supplies its
    own ``system`` prompt, the adapter only relays. Adding more verbs
    here would push business style choices into the adapter.
    """

    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
    ) -> str: ...


# ---------------------------------------------------------------------------
# OpenAI-compatible implementation
# ---------------------------------------------------------------------------

# Default base URLs for known providers. None = use the SDK default
# (api.openai.com). Extend this map when adding a new provider.
_DEFAULT_BASE_URLS: dict[str, str | None] = {
    "deepinfra": "https://api.deepinfra.com/v1/openai",
    "openai": None,
    "groq": "https://api.groq.com/openai/v1",
    "ollama": "http://localhost:11434/v1",
    "vllm": "http://localhost:8000/v1",
    "together": "https://api.together.xyz/v1",
}


class OpenAICompatibleLLMAdapter:
    """Single implementation for every OpenAI-compatible provider.

    Instantiate once per process via ``build_llm_adapter`` (factory below)
    rather than directly — the factory wires the right defaults from
    ``settings``.
    """

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        api_key: str,
        base_url: str | None = None,
        timeout_seconds: float = 60.0,
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

    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
    ) -> str:
        start = time.time()
        try:
            resp = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=max_tokens,
            )
        except (
            APITimeoutError,
            APIConnectionError,
            RateLimitError,
            InternalServerError,
        ) as exc:
            raise TransientLLMError(f"{type(exc).__name__}: {exc}") from exc
        except (
            AuthenticationError,
            PermissionDeniedError,
            BadRequestError,
            NotFoundError,
            UnprocessableEntityError,
        ) as exc:
            raise PermanentLLMError(f"{type(exc).__name__}: {exc}") from exc
        except APIStatusError as exc:
            # Other HTTP error not specifically typed by the SDK.
            if 500 <= exc.status_code < 600:
                raise TransientLLMError(f"HTTP {exc.status_code}: {exc}") from exc
            raise PermanentLLMError(f"HTTP {exc.status_code}: {exc}") from exc
        except APIError as exc:
            # Catch-all from the SDK base — treat as permanent to avoid
            # masking programming errors with hopeful retries.
            raise PermanentLLMError(f"{type(exc).__name__}: {exc}") from exc

        duration_ms = int((time.time() - start) * 1000)
        usage = getattr(resp, "usage", None)
        logger.info(
            "llm.complete",
            extra={
                "provider": self.provider,
                "model": self.model,
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "duration_ms": duration_ms,
            },
        )

        return resp.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Mock implementation (tests)
# ---------------------------------------------------------------------------


class MockLLMAdapter:
    """Deterministic, instant, no-network adapter for unit tests.

    The exact format is part of the public contract: tests assert against it.
    """

    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
    ) -> str:
        return f"[mock complete] system={system[:30]!r} user={user[:30]!r}"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_llm_adapter() -> LLMAdapter:
    """Build an adapter from current settings. Raises if config is invalid."""
    provider = settings.LLM_PROVIDER.strip().lower()
    if provider == "mock":
        return MockLLMAdapter()
    if provider not in _DEFAULT_BASE_URLS:
        raise RuntimeError(
            f"Unknown LLM_PROVIDER {provider!r}. "
            f"Supported: {sorted({*_DEFAULT_BASE_URLS, 'mock'})}"
        )
    if not settings.LLM_API_KEY and provider not in {"ollama", "vllm"}:
        # Local providers tolerate any value (often a placeholder); cloud
        # providers must have a real key.
        raise RuntimeError(
            f"LLM_API_KEY is required for provider {provider!r}. "
            "Set LLM_PROVIDER=mock for tests."
        )

    base_url = settings.LLM_BASE_URL or _DEFAULT_BASE_URLS[provider]
    return OpenAICompatibleLLMAdapter(
        provider=provider,
        model=settings.LLM_MODEL,
        api_key=settings.LLM_API_KEY or "noop",
        base_url=base_url,
        timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
    )


@lru_cache(maxsize=1)
def get_llm_adapter() -> LLMAdapter:
    """FastAPI / job dependency: process-wide adapter (memoised).

    Tests that change settings should call ``get_llm_adapter.cache_clear()``
    or override the dependency through ``app.dependency_overrides``.
    """
    return build_llm_adapter()
