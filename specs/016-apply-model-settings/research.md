# Research: Apply Model Settings to Generation Pipeline

## Decision 1: Keep cached getters, add explicit factory parameters

**Decision**: Keep `get_llm_adapter()` and `get_transcription_adapter()` memoized for environment-configured callers, and extend `build_llm_adapter(...)` / `build_transcription_adapter(...)` with optional explicit `provider`, `model`, `api_key`, and `base_url` parameters for per-user jobs.

**Rationale**: Existing tests and dependency injection rely on cached getters and cache clearing. Per-user routing needs fresh construction from saved settings, but only inside JDR jobs. This is the smallest change that fixes BD-19 without rewriting the adapter lifecycle.

**Alternatives considered**:

- Remove `@lru_cache`: rejected because it changes existing dependency-injection behavior and test assumptions without adding user value.
- Create a second adapter interface: rejected as premature; the existing Protocol already covers the required operations.

## Decision 2: Resolve session owner before adapter construction

**Decision**: For each JDR job, resolve the owning GM by campaign owner first, then by the session's GM API-key owner, then fall back to operator config if neither path resolves.

**Rationale**: Campaign ownership is the newer web-user visibility boundary, while API-key ownership preserves compatibility with legacy sessions. The fallback avoids breaking generation for old or machine-created sessions.

**Alternatives considered**:

- Require `campaign_id` for all historical sessions: rejected because existing nullable rows must remain readable/runnable.
- Fail when owner cannot be resolved: rejected because BD-19 requires env fallback for non-customized behavior.

## Decision 3: Treat local in-process providers as out of scope

**Decision**: If saved settings choose `local` for transcription or summary, BD-19 logs a warning and uses operator configuration.

**Rationale**: The handoff explicitly assigns local in-process execution to a future story. Implementing model loading or path validation here would expand scope and risk coupling jobs to model runtime details.

**Alternatives considered**:

- Validate local paths now: rejected because validation without execution would create a partial feature and UX ambiguity.
- Add local loader support now: rejected as scope creep.

## Decision 4: Persist Ollama model as a nullable account setting

**Decision**: Add nullable `ollama_model` to `jdr_model_settings`, expose it in read/write schemas, and apply it only when summary provider is `ollama`.

**Rationale**: Ollama is an LLM mode for Story 6.5, and the UI needs a stable model identifier to send and retrieve. Keeping it nullable preserves backward compatibility for existing settings rows.

**Alternatives considered**:

- Reuse `summary_cloud_model`: rejected because cloud model and Ollama model are distinct user choices and would make the response ambiguous.
- Store a general `summary_model`: rejected because it would force a broader migration/refactor than BD-19 needs.

## Decision 5: Return effective defaults without exposing secrets

**Decision**: When no settings row exists, compute a response from operator environment settings, include applicable provider/model values, and keep `deepinfra_api_key_set=false`.

**Rationale**: The settings screen should show what generation will actually use. Raw operator credentials must not become user-visible. This follows the project's environment-configured setup and 12-Factor config principle (https://12factor.net/config).

**Alternatives considered**:

- Keep Pydantic schema defaults: rejected because they can drift from real operator config.
- Expose whether an operator key exists: rejected because it is not the user's credential and adds little product value.

## Decision 6: Record effective model identity on artifacts

**Decision**: LLM artifacts should store `model_used` from the actual adapter route selected for the job. Transcription already stores `result.model_used` from the adapter result and should keep doing so.

**Rationale**: Existing artifact projections expose `model_used`; after BD-19, this must reflect the user's selected route for debugging and auditability.

**Alternatives considered**:

- Continue storing operator env values: rejected because it misreports per-user execution.
- Hide `model_used`: rejected because it is already part of the public contract and useful for support.
