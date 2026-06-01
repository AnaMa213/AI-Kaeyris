# Tasks: Timezone-Aware Datetime Serialization

**Input**: Design documents from `/specs/005-tz-aware-datetime-serialization/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/rest-api.md, quickstart.md

**Tests**: Required. The feature is a regression-prone API contract fix and FR-007 explicitly requires tests.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel with other tasks in the same phase when it touches different files and has no dependency on incomplete tasks.
- **[Story]**: Maps task to a user story from `spec.md`; setup, foundational, and polish tasks do not use story labels.
- Include exact file paths in every task description.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Prepare the minimal shared test and helper surface for the contract fix.

- [X] T001 Review existing datetime fields and response schemas in app/services/jdr/schemas.py and app/core/user_schemas.py against specs/005-tz-aware-datetime-serialization/contracts/rest-api.md
- [X] T002 [P] Create failing helper tests for naive, aware UTC, aware offset, microsecond, and None datetime cases in tests/core/test_datetime_serialization.py

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add the shared datetime serialization primitive before wiring any user story.

**CRITICAL**: No user story implementation can begin until this phase is complete.

- [X] T003 Implement `serialize_datetime_utc` and `ensure_aware_utc` helpers in app/core/datetime_serialization.py
- [X] T004 Run tests/core/test_datetime_serialization.py and confirm the new helper tests pass

**Checkpoint**: Shared serializer behavior is proven and ready for response schemas.

---

## Phase 3: User Story 1 - View JDR Session Times Without Silent Shift (Priority: P1) MVP

**Goal**: Session create/detail responses include explicit timezone suffixes and preserve the submitted instant.

**Independent Test**: Create a JDR session with a `Z` timestamp, read the creation and detail responses, and verify `recorded_at`, `created_at`, and `updated_at` all include explicit timezone suffixes and represent the same instant.

### Tests for User Story 1

> Write these tests first and confirm they fail before implementation.

- [X] T005 [US1] Add assertion helper for explicit timezone suffixes and UTC instant comparison in tests/services/jdr/test_datetime_serialization.py
- [X] T006 [US1] Add session create/detail timezone contract tests in tests/services/jdr/test_datetime_serialization.py

### Implementation for User Story 1

- [X] T007 [US1] Wire timezone-aware datetime serialization into SessionOut in app/services/jdr/schemas.py
- [X] T008 [US1] Normalize SessionCreate.recorded_at timezone handling for naive and offset inputs in app/services/jdr/schemas.py
- [X] T009 [US1] Run tests/services/jdr/test_datetime_serialization.py for US1 session create/detail coverage

**Checkpoint**: User Story 1 is independently functional and can be demoed as the MVP.

---

## Phase 4: User Story 2 - List Resources With Consistent Datetime Contract (Priority: P2)

**Goal**: Representative list/detail payloads for JDR resources and auth/user data use the same explicit-timezone response contract.

**Independent Test**: Request session, PJ, user, and auth context payloads and verify every non-null datetime string in covered responses includes `Z`, `+HH:MM`, or `-HH:MM`.

### Tests for User Story 2

- [X] T010 [US2] Add recursive payload datetime assertion tests for JDR session list responses in tests/services/jdr/test_datetime_serialization.py
- [X] T011 [US2] Add PJ create/list timezone contract tests in tests/services/jdr/test_datetime_serialization.py
- [X] T012 [US2] Add user create/list timezone contract tests in tests/services/jdr/test_datetime_serialization.py
- [X] T013 [P] [US2] Add auth/me datetime contract guard for any datetime fields present in tests/services/jdr/test_auth_me.py

### Implementation for User Story 2

- [X] T014 [P] [US2] Wire timezone-aware datetime serialization into all JDR response models with datetime fields in app/services/jdr/schemas.py
- [X] T015 [P] [US2] Wire timezone-aware datetime serialization into user/auth response models with datetime fields in app/core/user_schemas.py
- [X] T016 [US2] Run targeted route tests for datetime serialization in tests/services/jdr/test_datetime_serialization.py and tests/services/jdr/test_auth_me.py

**Checkpoint**: User Stories 1 and 2 both satisfy the public response contract independently.

---

## Phase 5: User Story 3 - Keep Existing Datetime Inputs Compatible (Priority: P3)

**Goal**: Existing datetime input variants remain accepted while all responses become explicit-timezone outputs.

**Independent Test**: Submit `recorded_at` with `Z`, numeric offset, and no timezone, then verify each request succeeds and the response represents the intended UTC instant with an explicit suffix.

### Tests for User Story 3

- [X] T017 [US3] Add session create input compatibility tests for `Z`, numeric offset, and timezone-naive recorded_at in tests/services/jdr/test_datetime_serialization.py

### Implementation for User Story 3

- [X] T018 [US3] Adjust datetime input normalization paths if compatibility tests expose gaps in app/services/jdr/schemas.py or app/services/jdr/logic.py
- [X] T019 [US3] Run targeted datetime compatibility tests in tests/services/jdr/test_datetime_serialization.py

**Checkpoint**: All accepted input variants continue working without reintroducing naive datetime responses.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Finish documentation and full quality validation.

- [X] T020 [P] Update docs/services/jdr.md with the explicit-timezone datetime response contract
- [X] T021 [P] Update docs/memo.md with the datetime serialization helper and test command
- [X] T022 [P] Add a dated BD-5 learning note to docs/journal.md
- [X] T023 Run `uv run pytest tests/core/test_datetime_serialization.py tests/services/jdr/test_datetime_serialization.py tests/services/jdr/test_auth_me.py -q`
- [X] T024 Run `uv run pytest`
- [X] T025 Run `uv run ruff check .`
- [X] T026 Manually verify the quickstart curl probe from specs/005-tz-aware-datetime-serialization/quickstart.md against a local API if the dev stack is running (skipped: no local API responded on localhost:8000)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: No dependencies.
- **Phase 2 Foundational**: Depends on Phase 1; blocks all user stories.
- **Phase 3 US1**: Depends on Phase 2; MVP.
- **Phase 4 US2**: Depends on Phase 2 and should run after US1 when working solo, because it reuses the same serializer/schema pattern.
- **Phase 5 US3**: Depends on Phase 2 and can run after US1 input handling is visible.
- **Phase 6 Polish**: Depends on all selected user stories.

### User Story Dependencies

- **US1 (P1)**: No dependency on other stories after Foundation.
- **US2 (P2)**: Technically independent after Foundation, but lower risk after US1 establishes the schema pattern.
- **US3 (P3)**: Depends on the same input boundary as US1, so implement after US1 when working sequentially.

### Within Each User Story

- Tests before implementation.
- Shared helper before schema wiring.
- Schema wiring before route-level verification.
- Targeted tests before full `pytest`.

---

## Parallel Opportunities

- T002 can run after T001 starts because it creates a new test file.
- T005 and T006 should be developed as one sequential test-focused batch because they share tests/services/jdr/test_datetime_serialization.py.
- T013 can run in parallel with the T010-T012 batch because it touches tests/services/jdr/test_auth_me.py.
- T020, T021, and T022 can run in parallel after behavior is implemented.

## Parallel Example: User Story 2

```text
Task: "Add auth/me datetime contract guard for any datetime fields present in tests/services/jdr/test_auth_me.py"
Task: "Wire timezone-aware datetime serialization into all JDR response models with datetime fields in app/services/jdr/schemas.py"
Task: "Wire timezone-aware datetime serialization into user/auth response models with datetime fields in app/core/user_schemas.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 and Phase 2.
2. Complete Phase 3 for session create/detail datetime output.
3. Run the targeted US1 tests.
4. Stop and validate the original BD-5 bug is fixed for `POST /services/jdr/sessions` and `GET /services/jdr/sessions/{id}`.

### Incremental Delivery

1. US1 fixes the observed session drift.
2. US2 extends the same contract across representative JDR/user/auth payloads.
3. US3 proves no existing datetime input contract broke.
4. Polish runs docs and full quality gates.

### Notes

- Do not add a database migration unless a failing test proves serialization cannot safely normalize current stored values.
- Do not add a third-party datetime dependency.
- Keep route handlers returning domain/schema objects; avoid manual datetime formatting inside route functions.
- Commit after a logical batch, preferably after green targeted tests for each user story.
