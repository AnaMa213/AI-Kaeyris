"""Generic LLM job — services pass their own system prompt.

ADR 0005 §9. Each future service (JDR, meeting summarizer, …) defines
its own system prompt in its own module and calls this job — no business
style ever lives in the adapter or in this generic job.
"""

import asyncio

from app.adapters.llm import (
    PermanentLLMError,
    TransientLLMError,
    get_llm_adapter,
)
from app.jobs import PermanentJobError, TransientJobError


def llm_complete(*, system: str, user: str, max_tokens: int = 500) -> str:
    """Call the configured LLM with ``(system, user)`` prompts.

    Adapter errors are remapped to the project's job-error hierarchy so
    RQ's retry policy (ADR 0004) applies the right behaviour.
    """
    adapter = get_llm_adapter()
    try:
        return asyncio.run(
            adapter.complete(system=system, user=user, max_tokens=max_tokens)
        )
    except TransientLLMError as exc:
        raise TransientJobError(str(exc)) from exc
    except PermanentLLMError as exc:
        raise PermanentJobError(str(exc)) from exc
