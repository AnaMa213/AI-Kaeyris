# Feature Specification: Local Model Validation

**Feature Branch**: `[017-local-model-validation]`  
**Created**: 2026-06-16  
**Status**: Draft  
**Input**: User description: "BD-20: add backend validation proof for local JDR model paths, enforce that proof when saving Local model settings, and make validated Local settings usable by generation jobs without silent fallback."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Validate a Local Model Path (Priority: P1)

As an administrator GM configuring local models, I want to submit a local model path for a specific category and receive a short-lived validation proof only when the model is loadable and task-compatible, so the settings screen can safely enable saving Local mode.

**Why this priority**: This is the frontend blocker in BD-20. Without a server-issued proof, the UI can only guess whether a local path is safe.

**Independent Test**: Call the local validation operation with transcription and summary paths, using controlled valid and invalid model directories, and verify that success returns an opaque proof while failures return user-safe Problem Details.

**Acceptance Scenarios**:

1. **Given** an administrator GM submits a readable transcription model path, **When** the backend validates it for transcription, **Then** the response contains a succeeded status, normalized path, runtime label, model format, opaque validation proof, and expiry timestamp.
2. **Given** an administrator GM submits a readable summary model path, **When** the backend validates it for summary, **Then** the response contains the same proof shape bound to the summary category.
3. **Given** the path is missing, unreadable, unsupported, task-incompatible, times out, or exceeds resource limits, **When** validation is requested, **Then** the backend rejects it with Problem Details that do not expose stack traces or raw runtime logs.

---

### User Story 2 - Save Local Settings Only With Proof (Priority: P1)

As an administrator GM, I want the settings save operation to reject changed Local paths unless I provide the matching unexpired proof, so direct API callers cannot bypass validation.

**Why this priority**: Validation alone is insufficient if a caller can PATCH Local settings without proving the path was validated.

**Independent Test**: Seed or obtain validation proofs for each category, then PATCH model settings with missing, wrong-category, wrong-path, expired, and valid proofs and verify only valid proofs allow Local path changes.

**Acceptance Scenarios**:

1. **Given** a GM changes `transcription_provider` to `local` with a new transcription path but no proof, **When** settings are saved, **Then** the request is rejected with a validation-required Problem Details response.
2. **Given** a GM provides a proof for a different path, category, user, status, or expired validation, **When** settings are saved, **Then** the request is rejected and no Local path change is persisted.
3. **Given** a GM provides a matching unexpired proof for the changed Local path, **When** settings are saved, **Then** the Local provider, path, and validation reference are persisted safely.

---

### User Story 3 - Run Jobs With Validated Local Settings (Priority: P2)

As an administrator GM who saved validated Local settings, I want transcription and generated artifact jobs to use the configured local runtimes, so Local mode is real execution behavior rather than a saved preference only.

**Why this priority**: This completes the backend contract after safe saving. It is lower than proof enforcement because it depends on the settings being safely persisted first.

**Independent Test**: Configure validated Local settings for a GM, run the job selection path for transcription and summary-related jobs with fake local runtime adapters, and verify the job uses Local mode or fails visibly when the saved Local configuration is broken.

**Acceptance Scenarios**:

1. **Given** a GM has validated Local transcription settings, **When** a transcription job runs for that GM's session, **Then** the job uses the validated local transcription runtime and path.
2. **Given** a GM has validated Local summary settings, **When** narrative, elements, POV, or summary jobs run for that GM's session, **Then** the job uses the validated local text-generation runtime and path.
3. **Given** a saved validated Local path fails at runtime, **When** the job runs, **Then** the job fails with a user-visible Problem Details-style error instead of silently falling back to operator defaults.
4. **Given** no owner or no model settings can be resolved, **When** a generation job runs, **Then** existing operator-default fallback behavior is preserved.

---

### User Story 4 - Understand Local Runtime Requirements (Priority: P3)

As the platform operator, I want local model validation and runtime limits documented, so I know which paths, formats, timeouts, and deployment mounts are supported before running the stack on the Raspberry Pi or a development host.

**Why this priority**: Documentation does not unblock the save flow by itself, but it prevents unsafe deployment assumptions once Local mode becomes executable.

**Independent Test**: Read the service documentation and quickstart, then verify the documented settings describe timeout, model formats, CPU/GPU expectations, dependency impact, and container path semantics.

**Acceptance Scenarios**:

1. **Given** an operator reads the JDR service docs, **When** they look for Local model setup, **Then** the docs identify supported transcription and summary formats, path expectations, timeout configuration, and resource caveats.
2. **Given** the frontend needs regenerated types, **When** backend OpenAPI is regenerated, **Then** the validation operation and proof fields are visible in the generated API contract.

### Edge Cases

- A validation proof is replayed by a different authenticated user.
- A validation proof is replayed for the opposite category.
- A path is syntactically the same but differs after normalization.
- A previously valid path is moved, deleted, or replaced after validation expires.
- A Local provider is already saved and the caller updates unrelated settings.
- A Local provider is already saved and the caller changes only one category path.
- A runtime raises a low-level exception containing filesystem or environment details.
- A validation request uses an extremely long path or an empty/blank path.
- The runtime dependency is not installed or disabled in the deployment image.
- A session owner cannot be resolved for a background job.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose a protected operation to validate a local model path for exactly one category: transcription or summary.
- **FR-002**: System MUST validate local model path input as a bounded non-empty string compatible with the existing Local path length limit.
- **FR-003**: System MUST normalize or canonicalize the submitted path consistently before binding it to a validation proof.
- **FR-004**: System MUST check that the submitted path exists and is readable before returning a successful proof.
- **FR-005**: System MUST verify that the submitted model is compatible with the requested category before returning a successful proof.
- **FR-006**: System MUST execute validation within a bounded timeout and return a safe timeout error when the probe exceeds that budget.
- **FR-007**: System MUST return a successful validation response containing an opaque `validation_id`, category, normalized path, succeeded status, runtime label, model format, user-safe message, and expiry timestamp.
- **FR-008**: System MUST bind each validation proof to the authenticated user, category, normalized path or path hash, success status, and expiry window.
- **FR-009**: System MUST reject Local path saves when a changed Local path lacks a matching unexpired validation proof.
- **FR-010**: System MUST reject validation proofs that belong to another user, category, path, status, or expired validation window.
- **FR-011**: System MUST add separate optional proof fields for transcription Local path saves and summary Local path saves.
- **FR-012**: System MUST preserve existing save behavior when the caller does not change an already-saved Local path and does not introduce a new Local path.
- **FR-013**: System MUST use validated Local transcription settings for owned transcription jobs.
- **FR-014**: System MUST use validated Local summary settings for owned narrative, elements, POV, and summary jobs.
- **FR-015**: System MUST preserve existing operator-default fallback behavior when no owner or settings can be resolved for a job.
- **FR-016**: System MUST NOT silently fall back to operator defaults when a saved validated Local setting is selected but the local runtime fails.
- **FR-017**: System MUST return RFC 9457-style Problem Details for validation and proof failures, including stable problem type identifiers.
- **FR-018**: System MUST NOT include secrets, absolute internal stack traces, or raw runtime logs in API responses.
- **FR-019**: System MUST regenerate the backend OpenAPI contract so frontend type generation can see the validation operation and proof fields.
- **FR-020**: System MUST document local model timeout, supported formats, CPU/GPU behavior, dependency impact, expected model path location, and container-vs-host path semantics.
- **FR-021**: System MUST include regression tests for validation success, validation failures, proof binding, proof expiry, PATCH enforcement, Local job routing, fallback preservation, and safe error bodies.

### Key Entities *(include if feature involves data)*

- **Local Model Validation Proof**: Short-lived server-issued proof that a specific user successfully validated a normalized path for one model category.
- **Model Settings**: Per-GM provider choices, Local paths, cloud/HTTP model identifiers, and Local validation proof references.
- **Local Model Path**: Operator-visible filesystem path or mounted container path submitted for validation.
- **JDR Generation Job**: Background operation that chooses transcription or text-generation runtime from the owning GM's effective settings.
- **Problem Details Error**: User-safe error payload with stable type, title, detail, and HTTP status for validation and runtime failures.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of Local path save attempts in the regression suite are rejected unless the matching user/category/path proof is present and unexpired.
- **SC-002**: 100% of validation failure scenarios in the regression suite return a stable Problem Details type without raw runtime logs or stack traces.
- **SC-003**: A successful validation response gives the frontend all fields needed to gate saving in a single request.
- **SC-004**: 100% of covered transcription and summary-related job routing tests use Local runtime selection when validated Local settings exist.
- **SC-005**: Existing unresolved-owner and missing-settings job routing tests continue to show operator-default fallback behavior.
- **SC-006**: The OpenAPI contract exposes the validation request, validation response, validation operation, and both Local validation proof fields.

## Assumptions

- The caller is an authenticated administrator GM using the existing JDR settings permissions.
- Local model paths are backend-visible paths, usually container-internal paths backed by host mounts in deployment.
- Validation proofs are short-lived because files can move or be replaced after validation.
- The first implementation may use bounded probes and optional runtime adapters, but it must expose honest failure when a required local runtime is unavailable.
- Full model discovery, model download, cost estimation, queue redesign, and GPU orchestration are outside BD-20.
- Frontend synchronization remains a separate repository step after backend OpenAPI generation.
