"""LLM adapter — vendor-neutral interface and OpenAI-compatible implementation.

ADR 0005. The interface (Protocol) hides the provider; one concrete class
covers all OpenAI-compatible providers (DeepInfra, Ollama, vLLM, Groq,
Together AI, OpenAI direct) by parameterising base_url + api_key + model.
"""

import time
from functools import lru_cache
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

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
    LLM_CALL_DURATION_SECONDS,
    LLM_CALLS_TOTAL,
    LLM_TOKENS_TOTAL,
)

logger = get_logger(__name__)


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
_UNSPECIFIED_IPV4 = ".".join(("0", "0", "0", "0"))
_LOOPBACK_HOSTS = {"localhost", _UNSPECIFIED_IPV4, "::1"}
_LOCAL_PROVIDERS = {"ollama", "vllm"}


def _running_in_container() -> bool:
    """Best-effort Docker/container detection for actionable config errors."""
    return Path("/.dockerenv").exists()


def _is_loopback_base_url(base_url: str) -> bool:
    host = urlparse(base_url).hostname
    return bool(host) and (host in _LOOPBACK_HOSTS or host.startswith("127."))


def _resolve_base_url(provider: str) -> str | None:
    base_url = settings.LLM_BASE_URL.strip() or _DEFAULT_BASE_URLS[provider]
    if base_url and _running_in_container() and _is_loopback_base_url(base_url):
        raise RuntimeError(
            "LLM_BASE_URL points to a loopback host from inside the worker "
            "container. Use a Docker-reachable host such as "
            "http://host.docker.internal:<port>/v1 or a Compose service name."
        )
    return base_url


def _llm_connectivity_error_message(
    *,
    exc: Exception,
    provider: str,
    model: str,
    base_url: str | None,
) -> str:
    endpoint = base_url or "OpenAI SDK default endpoint"
    message = (
        f"{type(exc).__name__}: cannot reach LLM provider {provider!r} "
        f"(model={model!r}, base_url={endpoint!r}): {exc}"
    )
    if provider in _LOCAL_PROVIDERS:
        message += (
            " Verify the local LLM server is running and reachable from the "
            "worker container. For a host-local server, use a Docker-reachable "
            "address such as http://host.docker.internal:<port>/v1."
        )
    return message


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
        self.base_url = base_url
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
        outcome = "success"
        try:
            try:
                resp = await self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    max_tokens=max_tokens,
                )
            except (APITimeoutError, APIConnectionError) as exc:
                outcome = "transient"
                raise TransientLLMError(
                    _llm_connectivity_error_message(
                        exc=exc,
                        provider=self.provider,
                        model=self.model,
                        base_url=self.base_url,
                    )
                ) from exc
            except (RateLimitError, InternalServerError) as exc:
                outcome = "transient"
                raise TransientLLMError(f"{type(exc).__name__}: {exc}") from exc
            except (
                AuthenticationError,
                PermissionDeniedError,
                BadRequestError,
                NotFoundError,
                UnprocessableEntityError,
            ) as exc:
                outcome = "permanent"
                raise PermanentLLMError(f"{type(exc).__name__}: {exc}") from exc
            except APIStatusError as exc:
                # Other HTTP error not specifically typed by the SDK.
                if 500 <= exc.status_code < 600:
                    outcome = "transient"
                    raise TransientLLMError(f"HTTP {exc.status_code}: {exc}") from exc
                outcome = "permanent"
                raise PermanentLLMError(f"HTTP {exc.status_code}: {exc}") from exc
            except APIError as exc:
                # Catch-all from the SDK base — treat as permanent to avoid
                # masking programming errors with hopeful retries.
                outcome = "permanent"
                raise PermanentLLMError(f"{type(exc).__name__}: {exc}") from exc
        finally:
            duration = time.time() - start
            LLM_CALL_DURATION_SECONDS.labels(
                provider=self.provider, model=self.model
            ).observe(duration)
            LLM_CALLS_TOTAL.labels(
                provider=self.provider, model=self.model, outcome=outcome
            ).inc()

        duration_ms = int(duration * 1000)
        usage = getattr(resp, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        if prompt_tokens:
            LLM_TOKENS_TOTAL.labels(
                provider=self.provider, model=self.model, direction="prompt"
            ).inc(prompt_tokens)
        if completion_tokens:
            LLM_TOKENS_TOTAL.labels(
                provider=self.provider, model=self.model, direction="completion"
            ).inc(completion_tokens)

        logger.info(
            "llm.complete",
            provider=self.provider,
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_ms=duration_ms,
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

    base_url = _resolve_base_url(provider)
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
