# Feature Specification: LLM Connectivity

**Feature Branch**: `codex/011-llm-connectivity`
**Created**: 2026-06-09
**Status**: Draft
**Input**: Backend handoff BD-11 asks to restore artifact-generation connectivity from the background worker to the language-model provider. Observed failure: summary jobs fail with `httpx.ConnectError: All connection attempts failed`, surfaced through `TransientLLMError: APIConnectionError: Connection error.`

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Generate JDR Artifacts End To End (Priority: P1)

As a GM using a transcribed non-diarised JDR session, I need summary generation to complete successfully so that the rest of the artifact chain can be validated and used.

**Why this priority**: This is the product blocker. If the background worker cannot reach the language-model provider, summaries, narratives, elements, POVs, regeneration flows, and frontend verification cannot be tested end to end.

**Independent Test**: Can be tested by starting from a transcribed non-diarised session, requesting a summary artifact, polling the job, and confirming that the job reaches success and the summary artifact becomes available.

**Acceptance Scenarios**:

1. **Given** a transcribed non-diarised JDR session, **When** the GM requests summary generation, **Then** the job eventually reaches `succeeded` and a summary artifact is available.
2. **Given** the same environment used by the application worker, **When** a summary job attempts to call the language-model provider, **Then** the call reaches the configured provider without connection failures.
3. **Given** a successful summary artifact exists, **When** downstream artifact jobs are requested, **Then** the artifact chain is no longer blocked by language-model connectivity.

---

### User Story 2 - Expose Actionable LLM Failure State (Priority: P2)

As a GM or frontend client, I need a failed language-model generation job to finish in a clear failed state with a readable reason so that the UI can display a retryable error instead of appearing stuck.

**Why this priority**: Even after connectivity is fixed, real provider outages can happen. A job that stays queued/running or has an empty failure reason creates a poor recovery path.

**Independent Test**: Can be tested by making the language-model provider unreachable, running an artifact job through its retry policy, and polling the job until it reaches a failed state with a non-empty reason.

**Acceptance Scenarios**:

1. **Given** the language-model provider is unreachable, **When** an artifact job exhausts retry attempts, **Then** the job reaches `failed` and exposes a non-empty failure reason.
2. **Given** a failed artifact job caused by provider connectivity, **When** the frontend reads the job details, **Then** the failure reason is stable and understandable enough to map to a retry message.
3. **Given** a transient provider issue later recovers, **When** the GM retries generation, **Then** the normal artifact generation flow can succeed without requiring data cleanup.

---

### User Story 3 - Avoid Transcription Regressions (Priority: P3)

As a GM, I need the existing transcription workflow to remain unaffected while language-model artifact generation is fixed.

**Why this priority**: Transcription is a separate pipeline capability and must remain reliable while artifact generation connectivity is adjusted.

**Independent Test**: Can be tested by running the existing transcription checks and verifying that transcription jobs still complete or fail according to their existing behavior.

**Acceptance Scenarios**:

1. **Given** an audio session ready for transcription, **When** transcription is run, **Then** its behavior remains unchanged by the language-model connectivity work.
2. **Given** language-model connectivity is unavailable, **When** a transcription-only job runs, **Then** it is not blocked by the language-model provider.

### Edge Cases

- The provider endpoint is unreachable from the worker environment but reachable from another process or host.
- The configured provider value is missing, malformed, or points to a loopback address that is valid only outside the worker runtime.
- The provider accepts TCP connections but rejects credentials or request shape; this must produce a clear failed job rather than a connectivity diagnosis.
- Retryable language-model failures recover before the retry budget is exhausted.
- Retryable language-model failures persist past the retry budget.
- Existing jobs created before the fix are polled after the fix.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The artifact-generation worker MUST be able to reach the configured language-model provider from the runtime environment used for background jobs.
- **FR-002**: A summary-generation job for a transcribed non-diarised session MUST be able to complete successfully when the provider is reachable and valid credentials are configured.
- **FR-003**: The summary job MUST expose a terminal successful status and make the generated summary available to existing readers when generation succeeds.
- **FR-004**: If the language-model provider remains unreachable after the retry policy is exhausted, the job MUST reach a terminal failed status.
- **FR-005**: Terminal failed artifact jobs caused by provider connectivity MUST expose a non-empty, readable failure reason.
- **FR-006**: The failure reason MUST distinguish provider connectivity problems from successful generation, validation errors, and unrelated transcription failures.
- **FR-007**: Existing public job polling behavior MUST remain compatible for frontend clients that already consume job status and failure reason.
- **FR-008**: The fix MUST NOT require a frontend contract change for requesting artifacts or polling jobs.
- **FR-009**: The transcription pipeline MUST continue to work without requiring language-model provider connectivity.
- **FR-010**: Operational documentation MUST identify the required language-model connectivity settings and the verification steps for the worker environment.

### Key Entities

- **Artifact Generation Job**: Background work item that generates summary, narrative, elements, or POV artifacts from an already prepared session document.
- **Language-Model Provider Configuration**: Runtime settings that determine where artifact generation sends model requests and which credentials are used.
- **Job Failure Reason**: Human-readable failure detail exposed through existing job polling so the frontend can display an actionable retry message.
- **Non-Diarised Session Summary**: First artifact in the non-diarised chain; downstream artifacts depend on it for end-to-end validation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In the target development environment, a summary-generation job for a valid transcribed non-diarised session reaches `succeeded` within the normal job polling window.
- **SC-002**: The previously observed provider connection failure no longer occurs during successful summary generation in the target environment.
- **SC-003**: With the provider intentionally unreachable, 100% of tested artifact jobs eventually reach `failed` rather than remaining indefinitely `queued` or `running`.
- **SC-004**: With the provider intentionally unreachable, 100% of tested failed artifact jobs expose a non-empty failure reason suitable for frontend retry messaging.
- **SC-005**: Existing transcription validation remains green after the language-model connectivity change.

## Assumptions

- The initial scope is backend and environment connectivity for artifact generation; no frontend contract change is required.
- The primary product blocker is worker-to-provider reachability, not prompt quality or artifact content quality.
- Existing job polling remains the user-visible progress and failure surface.
- Existing retry semantics remain acceptable unless planning uncovers that exhausted transient failures are not projected into the job status table.
- Valid provider credentials and a reachable provider are available in at least one target development environment for end-to-end verification.
