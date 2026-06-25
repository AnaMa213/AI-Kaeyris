# Tasks: Local Model Validation

**Input**: Design documents from `/specs/017-local-model-validation/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/rest-api.md, quickstart.md

**Tests**: Required by FR-021. Test tasks appear before implementation tasks for each user story.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it affects different files and has no dependency on another incomplete task.
- **[Story]**: User story label from spec.md.
- Every task names the target file or command path.

## Phase 1: Foundational Schema, Config, and Adapter Seams

**Purpose**: Add shared persistence/configuration/runtime seams needed before user-story work.

- [X] T001 [P] Add local validation config fields in app/core/config.py
- [X] T002 [P] Add optional `local` runtime extras in pyproject.toml
- [X] T003 Add `LocalModelValidation`, validation category/status enums, and proof-hash columns in app/services/jdr/db/models.py
- [X] T004 Add Alembic migration for validation table and settings proof columns in migrations/versions/0017_jdr_local_model_validation.py
- [X] T005 Add validation repository methods in app/services/jdr/db/repositories.py
- [X] T006 [P] Add local runtime probe module in app/adapters/local_models.py
- [X] T007 [P] Add Local LLM adapter factory support in app/adapters/llm.py
- [X] T008 [P] Add Local transcription adapter factory support in app/adapters/transcription.py

**Checkpoint**: The backend has persistence and adapter seams for Local validation without exposing routes yet.

---

## Phase 2: User Story 1 - Validate a Local Model Path (Priority: P1) MVP

**Goal**: Administrator GM can validate a local path and receive a short-lived proof, or get safe Problem Details.

**Independent Test**: Validation endpoint tests cover success and missing/unsupported/incompatible/timeout failures with monkeypatched probes.

### Tests for User Story 1

- [X] T009 [US1] Add failing local validation endpoint tests in tests/services/jdr/test_local_model_validation.py
- [X] T010 [US1] Add failing local probe unit tests in tests/adapters/test_local_models.py

### Implementation for User Story 1

- [X] T011 [US1] Add local validation request/response schemas in app/services/jdr/schemas.py
- [X] T012 [US1] Add local validation business service and Problem Details errors in app/services/jdr/local_model_validation.py
- [X] T013 [US1] Add `POST /services/jdr/settings/models/local/validation` in app/services/jdr/auth_router.py
- [X] T014 [US1] Run `pytest tests/services/jdr/test_local_model_validation.py tests/adapters/test_local_models.py -q`

**Checkpoint**: User Story 1 works independently for validation/proof creation.

---

## Phase 3: User Story 2 - Save Local Settings Only With Proof (Priority: P1)

**Goal**: Changed Local paths cannot be saved unless the matching proof is supplied and valid.

**Independent Test**: PATCH tests reject missing, expired, wrong-user, wrong-category, and wrong-path proofs while accepting valid proofs.

### Tests for User Story 2

- [X] T015 [US2] Add failing PATCH proof-enforcement tests in tests/services/jdr/test_model_settings.py

### Implementation for User Story 2

- [X] T016 [US2] Add write-only validation proof fields to `ModelSettingsPatch` in app/services/jdr/schemas.py
- [X] T017 [US2] Enforce proof validation before settings persistence in app/services/jdr/auth_router.py
- [X] T018 [US2] Store accepted proof hashes on model settings in app/services/jdr/db/repositories.py
- [X] T019 [US2] Run `pytest tests/services/jdr/test_model_settings.py -q`

**Checkpoint**: User Story 2 works independently for safe settings saves.

---

## Phase 4: User Story 3 - Run Jobs With Validated Local Settings (Priority: P2)

**Goal**: Jobs use saved validated Local runtimes and fail visibly instead of falling back silently when Local execution breaks.

**Independent Test**: Pipeline routing tests monkeypatch Local adapters and verify Local routing, explicit Local failure, and existing fallback for unresolved owner/settings.

### Tests for User Story 3

- [X] T020 [US3] Add failing Local job routing and no-silent-fallback tests in tests/services/jdr/test_pipeline_model_routing.py

### Implementation for User Story 3

- [X] T021 [US3] Route Local summary settings to the Local LLM adapter in app/jobs/jdr.py
- [X] T022 [US3] Route Local transcription settings to the Local transcription adapter in app/jobs/jdr.py
- [X] T023 [US3] Run `pytest tests/services/jdr/test_pipeline_model_routing.py -q`

**Checkpoint**: User Story 3 works independently for Local job execution routing.

---

## Phase 5: User Story 4 - Understand Local Runtime Requirements (Priority: P3)

**Goal**: Operators and frontend consumers can see the new contract and deployment requirements.

**Independent Test**: Docs and OpenAPI expose validation endpoint, PATCH proof fields, timeout/runtime settings, formats, and path semantics.

### Implementation for User Story 4

- [X] T024 [US4] Regenerate backend OpenAPI in docs/context/api/openapi.json
- [X] T025 [US4] Update JDR service documentation for Local validation/runtime requirements in docs/services/jdr.md
- [X] T026 [US4] Update command/field memo for BD-20 in docs/memo.md
- [X] T027 [US4] Add BD-20 learning journal entry in docs/journal.md

**Checkpoint**: User Story 4 works independently for docs and contract discovery.

---

## Phase 6: Polish and Cross-Cutting Concerns

**Purpose**: Validate migration health, linting, tests, and Spec Kit task completion.

- [X] T028 Run `alembic heads`
- [X] T029 Run `ruff check .`
- [X] T030 Run `pytest`
- [X] T031 Mark completed tasks in specs/017-local-model-validation/tasks.md

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1**: No dependency beyond existing BD-19 model settings code.
- **Phase 2 (US1)**: Depends on Phase 1 persistence and probe seams.
- **Phase 3 (US2)**: Depends on Phase 2 proof creation and Phase 1 repository support.
- **Phase 4 (US3)**: Depends on Phase 1 adapter seams and BD-19 job routing.
- **Phase 5 (US4)**: Depends on implemented endpoint/schema behavior.
- **Phase 6**: Depends on desired user stories and docs.

### User Story Dependencies

- **US1 (P1)**: MVP; can run after Phase 1.
- **US2 (P1)**: Depends on US1 proof records but can be tested by seeding proofs directly.
- **US3 (P2)**: Depends on saved Local settings and Local adapter factories.
- **US4 (P3)**: Depends on API/schema final shape.

### Parallel Opportunities

- T001 and T002 touch independent config/dependency files.
- T006, T007, and T008 touch separate adapter files after models/repository shape is known.
- T009 and T010 target different test modules.
- T024, T025, T026, and T027 touch separate documentation/contract files after code stabilizes.

## Parallel Example: User Story 1

```text
Task: "Add failing local validation endpoint tests in tests/services/jdr/test_local_model_validation.py"
Task: "Add failing local probe unit tests in tests/adapters/test_local_models.py"
```

## Implementation Strategy

### MVP First

1. Complete Phase 1.
2. Complete User Story 1 and run its focused tests.
3. Complete User Story 2 so the validation proof actually gates saving.
4. Stop and validate frontend-unblocking API contract.

### Incremental Delivery

1. Add schema and persistence.
2. Add validation endpoint and proof creation.
3. Enforce proofs on PATCH.
4. Wire Local job execution.
5. Regenerate OpenAPI and update docs.

## Notes

- Local runtime packages are optional and lazy; tests must not require real model downloads.
- Proof values are write-only and must not be logged.
- Existing `.claude/` local changes and BD-19 dirty files are treated as pre-existing work and must not be reverted.
