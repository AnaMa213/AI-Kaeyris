# Tasks: Apply Model Settings to Generation Pipeline

**Input**: Design documents from `/specs/016-apply-model-settings/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/rest-api.md, quickstart.md

**Tests**: Required by FR-015. Test tasks appear before implementation tasks for each user story.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it affects different files and has no dependency on another incomplete task.
- **[Story]**: User story label from spec.md.
- Every task names the target file or command path.

## Phase 1: Foundational Schema and Contracts

**Purpose**: Add the shared field and schema surface needed by all user stories.

- [X] T001 [P] Add `ollama_model` column to `ModelSettings` in app/services/jdr/db/models.py
- [X] T002 [P] Create Alembic migration for `jdr_model_settings.ollama_model` in migrations/versions/0016_jdr_model_settings_ollama_model.py
- [X] T003 [P] Add `ollama_model` to `ModelSettingsOut` and `ModelSettingsPatch` in app/services/jdr/schemas.py
- [X] T004 Wire `ollama_model` through model settings persistence and serialization in app/services/jdr/db/repositories.py and app/services/jdr/auth_router.py

**Checkpoint**: Model-settings payloads can carry the new Ollama model field.

---

## Phase 2: User Story 1 - Generate With GM Model Settings (Priority: P1) MVP

**Goal**: Every JDR job uses the owning GM's effective model settings, with safe fallback to operator configuration.

**Independent Test**: The new pipeline routing test module verifies paid cloud, free cloud, Ollama, local fallback, and owner-resolution scenarios without real network calls.

### Tests for User Story 1

- [X] T005 [US1] Add failing adapter routing and owner-resolution tests in tests/services/jdr/test_pipeline_model_routing.py

### Implementation for User Story 1

- [X] T006 [P] Extend explicit-parameter LLM adapter factory while preserving cached getter behavior in app/adapters/llm.py
- [X] T007 [P] Extend explicit-parameter transcription adapter factory while preserving cached getter behavior in app/adapters/transcription.py
- [X] T008 [US1] Add session-owner, user-settings, and effective model helper functions in app/jobs/jdr.py
- [X] T009 [US1] Use per-user transcription adapter selection in `_transcribe_session` in app/jobs/jdr.py
- [X] T010 [US1] Use per-user LLM adapter selection and effective `model_used` in narrative, elements, POV, and summary jobs in app/jobs/jdr.py
- [X] T011 [US1] Run `pytest tests/services/jdr/test_pipeline_model_routing.py -q`

**Checkpoint**: User Story 1 works independently for job routing.

---

## Phase 3: User Story 2 - See Effective Defaults Safely (Priority: P2)

**Goal**: Administrator GMs without saved settings see effective operator defaults without any raw secret exposure.

**Independent Test**: The settings endpoint tests verify no-row defaults reflect environment config and never include raw keys.

### Tests for User Story 2

- [X] T012 [US2] Add failing effective-default and no-secret response tests in tests/services/jdr/test_model_settings.py

### Implementation for User Story 2

- [X] T013 [US2] Return effective environment defaults for missing model-settings rows in app/services/jdr/auth_router.py
- [X] T014 [US2] Regenerate OpenAPI schema for settings response changes in docs/context/api/openapi.json
- [X] T015 [US2] Run `pytest tests/services/jdr/test_model_settings.py -q`

**Checkpoint**: User Story 2 works independently for settings reads.

---

## Phase 4: User Story 3 - Persist Ollama Model Choice (Priority: P3)

**Goal**: Administrator GMs can save and retrieve an Ollama model name for summary generation.

**Independent Test**: PATCH then GET model settings with `summary_provider=ollama` and `ollama_model`, verifying persistence and safe response shape.

### Tests for User Story 3

- [X] T016 [US3] Add failing PATCH/GET `ollama_model` persistence tests in tests/services/jdr/test_model_settings.py

### Implementation for User Story 3

- [X] T017 [US3] Complete `ollama_model` PATCH logging and response behavior in app/services/jdr/auth_router.py
- [X] T018 [US3] Run `pytest tests/services/jdr/test_model_settings.py -q`

**Checkpoint**: User Story 3 works independently for persisted Ollama model choice.

---

## Phase 5: Polish and Cross-Cutting Concerns

**Purpose**: Keep docs, contracts, and quality gates aligned with the implementation.

- [X] T019 [P] Update JDR service documentation for BD-19 model routing in docs/services/jdr.md
- [X] T020 [P] Update command/field memo for BD-19 in docs/memo.md
- [X] T021 [P] Add BD-19 learning journal entry in docs/journal.md
- [X] T022 Run `alembic heads` to verify a single migration head
- [X] T023 Run `ruff check .`
- [X] T024 Run `pytest`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1**: No dependency beyond existing BD-18 settings code.
- **Phase 2 (US1)**: Depends on Phase 1 field/schema availability for row fixtures.
- **Phase 3 (US2)**: Depends on Phase 1 response shape.
- **Phase 4 (US3)**: Depends on Phase 1 persistence shape.
- **Phase 5**: Depends on completed desired user stories.

### User Story Dependencies

- **US1 (P1)**: MVP; can run after Phase 1.
- **US2 (P2)**: Can run after Phase 1 and does not require US1 internals.
- **US3 (P3)**: Can run after Phase 1 and complements US2.

### Parallel Opportunities

- T001, T002, and T003 touch distinct files.
- T006 and T007 touch distinct adapter files.
- T019, T020, and T021 touch distinct documentation files.

## Parallel Example: User Story 1

```text
Task: "Extend explicit-parameter LLM adapter factory while preserving cached getter behavior in app/adapters/llm.py"
Task: "Extend explicit-parameter transcription adapter factory while preserving cached getter behavior in app/adapters/transcription.py"
```

## Implementation Strategy

### MVP First

1. Complete Phase 1.
2. Complete User Story 1 and run its focused tests.
3. Validate that no-row and legacy sessions still fall back to operator configuration.

### Incremental Delivery

1. Add schema/migration support.
2. Add per-user pipeline routing.
3. Fix settings read defaults.
4. Persist Ollama model choice.
5. Regenerate OpenAPI and update docs.

## Notes

- Local in-process model execution remains out of scope for BD-19.
- Raw keys must not appear in tests, logs, docs examples, or OpenAPI response schemas.
- Existing `.claude/` local changes are unrelated and must not be reverted.
