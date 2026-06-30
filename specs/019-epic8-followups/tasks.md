# Tasks: Epic 8 Follow-ups

**Input**: Design documents from `specs/019-epic8-followups/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/rest-api.md, quickstart.md  
**Tests**: Required by the feature specification for every user story because all changed behaviors are public API or persistence contracts.  
**Organization**: Tasks are grouped by user story so each review follow-up can be implemented and verified independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it touches a different file or is read-only.
- **[Story]**: Maps to a user story from `spec.md`.
- Every task references exact repository paths.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the active Spec Kit context and keep design artifacts clean before implementation.

- [X] T001 Verify the active feature pointer in `.specify/feature.json` and checklist status in `specs/019-epic8-followups/checklists/requirements.md`
- [X] T002 Normalize portable ASCII structure blocks in `specs/019-epic8-followups/plan.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Read the existing JDR seams that all user stories depend on.

- [X] T003 [P] Inspect existing manual artifact edit routes and job projection helpers in `app/services/jdr/router.py`
- [X] T004 [P] Inspect existing player participation helpers in `app/services/jdr/logic.py`
- [X] T005 [P] Inspect artifact provenance model and repository behavior in `app/services/jdr/db/models.py` and `app/services/jdr/db/repositories.py`
- [X] T006 [P] Inspect current JDR test fixtures and route patterns in `tests/conftest.py` and `tests/services/jdr/`

**Checkpoint**: Foundation understood; user story implementation can begin.

---

## Phase 3: User Story 1 - Prevent Concurrent Edit Loss (Priority: P1) MVP

**Goal**: Reject manual artifact edits while an active artifact-generation job can still overwrite the session artifacts.

**Independent Test**: Seed an active artifact job for a session, call each manual edit endpoint, assert `409 artifact-busy`, and assert stored artifacts are unchanged.

### Tests for User Story 1

- [X] T007 [US1] Add busy-generation conflict coverage for summary, narrative, POV, and elements edits in `tests/services/jdr/test_artifact_edit.py`
- [X] T008 [US1] Add regression coverage proving non-artifact active jobs do not block artifact edits in `tests/services/jdr/test_artifact_edit.py`

### Implementation for User Story 1

- [X] T009 [US1] Add `artifact-busy` error handling and an active artifact job guard in `app/services/jdr/router.py`
- [X] T010 [US1] Wire the active artifact job guard into summary, narrative, POV, and elements edit routes in `app/services/jdr/router.py`

**Checkpoint**: User Story 1 is independently functional and testable.

---

## Phase 4: User Story 2 - Prevent Accidental Elements Wipe (Priority: P1)

**Goal**: Preserve atomic replacement semantics while requiring explicit confirmation for clearing all elements.

**Independent Test**: Submit `{"elements":[]}` without confirmation and with `?confirm_empty=true`, verifying only the confirmed clear mutates the artifact.

### Tests for User Story 2

- [X] T011 [P] [US2] Add unconfirmed and confirmed empty elements replacement coverage in `tests/services/jdr/test_artifact_elements_freeform.py`

### Implementation for User Story 2

- [X] T012 [US2] Add `confirm_empty` query validation to the elements replacement route in `app/services/jdr/router.py`

**Checkpoint**: User Story 2 is independently functional and testable.

---

## Phase 5: User Story 3 - Define Player Scope In Non-Diarised Sessions (Priority: P2)

**Goal**: Use player-presence rows for non-diarised player reads while preserving diarised speaker-mapping behavior.

**Independent Test**: Create non-diarised sessions with and without a player's PJ in the presence list, then verify `/me` listing and shared artifact reads match that scope.

### Tests for User Story 3

- [X] T013 [P] [US3] Add non-diarised player artifact read coverage in `tests/services/jdr/test_player_artifact_reads.py`
- [X] T014 [P] [US3] Add non-diarised player session listing coverage in `tests/services/jdr/test_player_listing.py`

### Implementation for User Story 3

- [X] T015 [US3] Implement mode-aware session listing in `app/services/jdr/logic.py`
- [X] T016 [US3] Implement mode-aware player read authorization in `app/services/jdr/logic.py`

**Checkpoint**: User Story 3 is independently functional and testable.

---

## Phase 6: User Story 4 - Keep Artifact Defaults Consistent (Priority: P3)

**Goal**: Align generated artifact defaults and provenance cleanup with migration behavior.

**Independent Test**: Create a generated artifact without manual edit fields and verify it is treated as unedited; manually edited artifacts still record provenance.

### Tests for User Story 4

- [X] T017 [P] [US4] Add artifact provenance default regression coverage in `tests/services/jdr/test_artifact_provenance.py`

### Implementation for User Story 4

- [X] T018 [US4] Align `Artifact.is_edited` server default with migration false semantics in `app/services/jdr/db/models.py`
- [X] T019 [US4] Remove function-local datetime imports and reuse module-level timestamp handling in `app/services/jdr/db/repositories.py`

**Checkpoint**: User Story 4 is independently functional and testable.

---

## Phase 7: User Story 5 - Bound Pathological Text Edits (Priority: P4)

**Goal**: Accept long realistic RPG text edits while rejecting payloads above a documented generous safety limit.

**Independent Test**: Submit a long realistic edit and an over-limit edit, verifying the former succeeds and the latter returns `422` without mutation.

### Tests for User Story 5

- [X] T020 [P] [US5] Add long-edit and over-limit edit coverage in `tests/services/jdr/test_artifact_text_length.py`

### Implementation for User Story 5

- [X] T021 [US5] Add a documented maximum text edit length to `TextEditIn` in `app/services/jdr/schemas.py`

**Checkpoint**: User Story 5 is independently functional and testable.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Validate the whole follow-up set and prepare a clean delivery.

- [X] T022 Run focused quickstart tests from `specs/019-epic8-followups/quickstart.md`
- [X] T023 Run `ruff check .` from the repository root
- [X] T024 Run the full JDR service test suite with `.venv/Scripts/python.exe -m pytest tests/services/jdr -q --tb=short`
- [X] T025 Update completed task checkboxes in `specs/019-epic8-followups/tasks.md`
- [X] T026 Review the final diff with `git diff --stat` and `git diff --check`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks all user story work.
- **User Stories (Phase 3+)**: Depend on Foundational completion. They can be implemented independently after that, but this workflow runs them in priority order.
- **Polish (Phase 8)**: Depends on all implemented user stories.

### User Story Dependencies

- **US1 (P1)**: No dependency on other user stories.
- **US2 (P1)**: No dependency on US1, but touches the same elements route guard area, so implement after US1 in this single-agent workflow.
- **US3 (P2)**: No dependency on US1 or US2.
- **US4 (P3)**: No dependency on route behavior.
- **US5 (P4)**: No dependency on route guards; validation is shared by text edit endpoints.

### Parallel Opportunities

- T003, T004, T005, and T006 are read-only and can run in parallel.
- T011 can run while US1 route tests are being written if file ownership is split.
- T013 and T014 can run in parallel because they touch different test files.
- T017 and T020 can run in parallel with US3 implementation after foundational inspection.

---

## Parallel Example: User Story 3

```text
Task: "Add non-diarised player artifact read coverage in tests/services/jdr/test_player_artifact_reads.py"
Task: "Add non-diarised player session listing coverage in tests/services/jdr/test_player_listing.py"
```

---

## Implementation Strategy

### MVP First

1. Complete Phase 1 and Phase 2.
2. Implement US1.
3. Run `tests/services/jdr/test_artifact_edit.py` to validate the lost-update guard.

### Incremental Delivery

1. Add US2 and run elements edit tests.
2. Add US3 and run player-read/listing tests.
3. Add US4 and US5 cleanup/hardening with their focused tests.
4. Run the quickstart validation, ruff, and the full JDR suite.

### Quality Notes

- Write tests before implementation for each changed public behavior.
- Keep all changes inside `app/services/jdr` and `tests/services/jdr`.
- Do not add a migration unless a real schema change appears during implementation.
