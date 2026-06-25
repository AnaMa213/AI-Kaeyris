# Feature Specification: Apply Model Settings to Generation Pipeline

**Feature Branch**: `[016-apply-model-settings]`  
**Created**: 2026-06-16  
**Status**: Draft  
**Input**: User description: "BD-19: wire persisted per-GM model settings into the JDR generation pipeline for paid cloud, operator-provided cloud, and Ollama HTTP LLM usage."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Generate With GM Model Settings (Priority: P1)

As a GM who configured model preferences, I want every JDR transcription and generated artifact job for my sessions to use my effective model settings, so selecting a model actually changes the generated output path instead of being ignored.

**Why this priority**: This closes the main BD-19 defect: persisted model settings exist, but generation still behaves as if only operator defaults exist.

**Independent Test**: Create sessions owned through both campaign ownership and API-key ownership, configure each supported setting posture, run the job selection path, and verify the selected adapter/model identity matches the GM's effective settings.

**Acceptance Scenarios**:

1. **Given** a GM has a paid cloud key and selected cloud models, **When** a transcription or summary-related job runs for one of their sessions, **Then** the job uses the GM's key and selected model for the matching job type.
2. **Given** a GM selected cloud mode but has no personal key, **When** a transcription or summary-related job runs, **Then** the job keeps using the operator-provided cloud configuration.
3. **Given** a GM selected Ollama and an Ollama model for summaries, **When** a summary-related job runs, **Then** the LLM call uses the Ollama model over an HTTP-compatible local endpoint.
4. **Given** a GM selected local in-process mode, **When** a BD-19 job runs, **Then** the job falls back to the operator configuration and logs that in-process local model execution is outside this story.

---

### User Story 2 - See Effective Defaults Safely (Priority: P2)

As an administrator GM without saved model settings, I want the model settings endpoint to show the effective operator defaults, so the UI reflects what generation will actually use.

**Why this priority**: Without this, the settings screen can display placeholder defaults that differ from the real runtime configuration.

**Independent Test**: Sign in as an administrator GM with no saved model-settings row, request model settings, and verify the returned providers/models match the effective operator configuration while no raw secret is present.

**Acceptance Scenarios**:

1. **Given** an administrator GM has no saved model settings, **When** they request model settings, **Then** the response contains the effective transcription and summary providers and their applicable model names.
2. **Given** the operator has configured cloud credentials, **When** any GM requests model settings, **Then** the response never exposes the raw operator key and reports no personal key for users without one.

---

### User Story 3 - Persist Ollama Model Choice (Priority: P3)

As an administrator GM, I want to save and retrieve the Ollama model name for summaries, so the settings UI can configure the local HTTP LLM mode end to end.

**Why this priority**: Ollama is one of the Story 6.5 supported modes, and the backend currently lacks a persisted model-name field for it.

**Independent Test**: Patch the model settings with an Ollama summary provider and model name, then fetch settings and verify the same model name is returned without exposing any secret.

**Acceptance Scenarios**:

1. **Given** an administrator GM submits an Ollama model name, **When** settings are saved, **Then** the model name is persisted for that GM.
2. **Given** an Ollama model name was saved, **When** the GM fetches model settings later, **Then** the same model name is returned.

---

### Edge Cases

- A session has no campaign owner but was created through a GM API key: ownership falls back to the API-key owner.
- A session has no resolvable owner: generation uses the operator configuration rather than failing because of missing account settings.
- A row exists with local in-process providers: BD-19 does not execute local in-process models and falls back to operator configuration.
- A row contains an Ollama provider for transcription: transcription does not use Ollama and falls back to operator configuration.
- A GM has no saved settings row: the settings response reflects effective defaults, not schema placeholder values.
- Operator secrets and personal cloud keys are never serialized in settings responses or logs.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST resolve the owning GM for a JDR session before selecting model settings, preferring the campaign owner and falling back to the session's GM API-key owner.
- **FR-002**: System MUST load the owning GM's saved model settings for transcription, narrative, elements, POV, and summary jobs.
- **FR-003**: System MUST preserve current operator-configuration behavior when no owner or no saved settings exist.
- **FR-004**: System MUST route paid cloud usage through the GM's personal cloud key and selected model when a personal key is present.
- **FR-005**: System MUST route free cloud usage through the operator configuration when the GM selected cloud mode without a personal key.
- **FR-006**: System MUST route Ollama summary usage through the selected Ollama model and an HTTP-compatible local LLM endpoint.
- **FR-007**: System MUST NOT route transcription through Ollama; unsupported transcription choices MUST fall back to the operator configuration.
- **FR-008**: System MUST keep local in-process model execution out of BD-19 and fall back to the operator configuration for local providers.
- **FR-009**: System MUST record generated artifact model identifiers using the effective provider and model used for that job.
- **FR-010**: System MUST keep process-wide operator-configured adapter access available for existing dependency-injection and non-per-user callers.
- **FR-011**: System MUST return effective operator defaults from the model settings read endpoint when the GM has no saved settings row.
- **FR-012**: System MUST persist and return an optional Ollama model name for account-level model settings.
- **FR-013**: System MUST NOT expose raw operator keys or raw personal cloud keys in any model settings response.
- **FR-014**: System MUST validate the Ollama model name with the same bounded-string discipline as other model identifiers.
- **FR-015**: System MUST include regression tests covering owner resolution, adapter routing, safe settings serialization, and Ollama model persistence.

### Key Entities *(include if feature involves data)*

- **Model Settings**: Per-GM provider choices, cloud model identifiers, optional personal cloud key presence, local path placeholders, and optional Ollama summary model.
- **JDR Session**: Session whose owner determines which model settings apply to generation.
- **Campaign**: Preferred ownership source for a session.
- **GM API Key**: Fallback ownership source when a session is not tied to a campaign.
- **Generation Job**: Background operation that consumes transcription or LLM settings and stores the effective model identity in generated outputs.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of covered JDR generation job types select the GM's effective settings in paid cloud, free cloud, Ollama, and fallback scenarios.
- **SC-002**: 100% of settings responses in the regression suite omit raw secret fields while still exposing whether a personal key exists.
- **SC-003**: A GM without saved settings can see the effective operator provider/model values in one settings request.
- **SC-004**: Existing environment-default generation behavior remains unchanged for sessions without resolvable user settings.
- **SC-005**: The JDR model-settings and pipeline regression test suites pass with no linter errors.

## Assumptions

- BD-19 covers Story 6.5 only: paid cloud, operator-provided cloud, and Ollama HTTP for LLM summaries.
- Local in-process model loading, model discovery, cost estimation, and at-rest key encryption remain follow-up work.
- The existing authentication and administrator-only settings access rules remain unchanged.
- Existing operator configuration remains the source of truth when no personal setting applies.
