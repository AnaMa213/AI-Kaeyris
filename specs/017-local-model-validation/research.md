# Research: Local Model Validation

## Decision 1: Store validation proofs server-side as hashes

**Decision**: Generate an opaque random `validation_id`, return it once to the client, and store only `sha256(validation_id)` with user/category/path hash/status/expiry metadata.

**Rationale**: The backend can revoke proofs naturally through expiry and can verify replay attempts without introducing a new signing secret. Hash storage reduces blast radius if the table is inspected.

**Alternatives considered**:

- Signed stateless token: avoids a table but requires key rotation and a new secret lifecycle.
- Store raw proof IDs: simpler, but leaked database rows would be directly replayable until expiry.

## Decision 2: Add a dedicated validation table plus nullable settings references

**Decision**: Add `jdr_local_model_validations` and nullable `transcription_local_validation_hash` / `summary_local_validation_hash` columns on `jdr_model_settings`.

**Rationale**: Validation events are independent, expiring proofs; model settings hold the currently accepted reference. Additive nullable columns preserve existing rows.

**Alternatives considered**:

- Store proof metadata only on model settings: cannot represent multiple attempts or reject stale proofs cleanly.
- Keep proofs in memory: fails across API processes and restarts.

## Decision 3: Lazy optional local runtime imports

**Decision**: Implement real adapter/probe seams for local transcription and local text generation, but import `faster-whisper` and `llama-cpp-python` only when Local validation or Local execution is requested.

**Rationale**: The project can keep the normal backend install fast and deterministic while making Local mode honest: unavailable runtime packages return explicit errors. `faster-whisper` documents a CTranslate2-backed Whisper implementation with CPU/GPU considerations (https://github.com/SYSTRAN/faster-whisper). `llama-cpp-python` provides Python bindings over `llama.cpp` (https://github.com/abetlen/llama-cpp-python), whose upstream project targets LLM inference in C/C++ (https://github.com/ggml-org/llama.cpp).

**Alternatives considered**:

- Add heavy runtime packages to default dependencies: easier to call, but increases install/image cost for users who do not use Local mode.
- Fake validation through filesystem checks only: unblocks UI but violates BD-20's backend validation intent.

## Decision 4: Category-specific compatibility checks before runtime load

**Decision**: Require transcription paths to look like local Whisper/CTranslate2 directories and summary paths to point to GGUF files before attempting runtime initialization.

**Rationale**: Cheap format checks give clearer errors and avoid loading obviously incompatible assets. Runtime initialization remains the final validation step.

**Alternatives considered**:

- Runtime-only validation: simpler code, but slower and produces less predictable errors.
- Extension-only validation: fast, but insufficient to prove loadability.

## Decision 5: RFC 9457-style errors with stable local-model types

**Decision**: Use existing `AppError`/Problem Details infrastructure for validation failures, with stable types such as `local-model-path-not-found`, `local-model-timeout`, `local-model-incompatible-task`, `local-model-unsupported-format`, `local-model-validation-expired`, and `local-model-validation-required`.

**Rationale**: The frontend contract explicitly displays `title` and `detail`, and RFC 9457 defines a standard Problem Details shape for HTTP APIs (https://www.rfc-editor.org/rfc/rfc9457.html).

**Alternatives considered**:

- Plain 400 JSON errors: less consistent with the backend's existing error policy.
- Raw runtime exception bodies: unsafe and not user-facing.

## Decision 6: Explicit Local job failure once Local is saved

**Decision**: Preserve operator fallback only for unresolved owner/settings. If a GM explicitly saved Local and the local runtime fails, the job fails permanently with a user-safe reason.

**Rationale**: Silent fallback would make the saved Local setting misleading and would hide broken deployment paths from the GM.

**Alternatives considered**:

- Continue fallback on any Local failure: resilient, but violates BD-20's "must not silently fall back" requirement.
- Fail even when owner/settings are missing: would regress BD-19 fallback behavior.
