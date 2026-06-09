# Tasks: BD-12 PJ Update

**Input**: Design documents from `specs/012-pj-update/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/rest-api.md`, `quickstart.md`

**Tests**: Included because `spec.md` defines independent test criteria for each user story and the project constitution requires every public endpoint to have at least one test.

**Organization**: Tasks are grouped by user story so rename, user-link editing, and ownership isolation can each be implemented and tested independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it touches a different file or only adds independent tests.
- **[Story]**: Maps the task to the user story from `spec.md`.
- Every task includes an exact repository file path.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the BD-12 workspace and keep the implementation scoped to the existing JDR service.

- [X] T001 Verify the workspace is on branch `codex/012-pj-update` and that `specs/012-pj-update/plan.md` is the active Spec Kit plan in `AGENTS.md`
- [X] T002 [P] Review current PJ schema and route behavior in `app/services/jdr/schemas.py` and `app/services/jdr/router.py`
- [X] T003 [P] Review current PJ persistence and duplicate-name handling in `app/services/jdr/logic.py` and `app/services/jdr/db/repositories.py`
- [X] T004 [P] Review current PJ endpoint fixtures and helper patterns in `tests/services/jdr/test_pjs.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add the shared request/error plumbing required by every BD-12 user story.

**CRITICAL**: No user story implementation should start until the shared PATCH schema and invalid-user contract are defined.

- [X] T005 Add `PjUpdate` request schema with optional `name` and optional nullable `user_id` in `app/services/jdr/schemas.py`
- [X] T006 Add or reuse an app-level error class that maps PJ user-assignment failures to `422 invalid-user` in `app/services/jdr/router.py`
- [X] T007 Update the existing `POST /services/jdr/pjs` `PjAssignmentError` handling to return `422 invalid-user` in `app/services/jdr/router.py`
- [X] T008 [P] Add a regression test proving `POST /services/jdr/pjs` with an unknown non-null `user_id` returns `422 invalid-user` in `tests/services/jdr/test_pjs.py`
- [X] T009 Add a reusable `logic.update_pj` function signature that accepts `pj_id`, `gm_key_id`, optional `campaign_id`, optional `requester_user_id`, and field-presence-aware update data in `app/services/jdr/logic.py`

**Checkpoint**: Shared schema and error contract are ready; user story work can begin.

---

## Phase 3: User Story 1 - Rename A Player Character (Priority: P1) MVP

**Goal**: A GM can rename an owned PJ without recreating it, and duplicate names are rejected.

**Independent Test**: Create two PJs for the same GM, PATCH the name of one, confirm only that PJ changes and duplicate rename returns `409 duplicate-pj`.

### Tests for User Story 1

- [X] T010 [P] [US1] Add a failing test for successful PJ rename returning updated `PjOut` in `tests/services/jdr/test_pjs.py`
- [X] T011 [P] [US1] Add a failing test that renaming one PJ does not modify another PJ owned by the same GM in `tests/services/jdr/test_pjs.py`
- [X] T012 [P] [US1] Add a failing test that renaming to an existing same-GM PJ name returns `409 duplicate-pj` in `tests/services/jdr/test_pjs.py`

### Implementation for User Story 1

- [X] T013 [US1] Implement owned PJ loading and name assignment in `logic.update_pj` in `app/services/jdr/logic.py`
- [X] T014 [US1] Flush duplicate-name violations and map `DuplicatePjNameError` to `DuplicatePjError` in `app/services/jdr/logic.py`
- [X] T015 [US1] Add `PATCH /services/jdr/pjs/{pj_id}` route that calls `logic.update_pj` and returns `PjOut` in `app/services/jdr/router.py`
- [X] T016 [US1] Map rename duplicate failures from `DuplicatePjError` to existing `409 duplicate-pj` in `app/services/jdr/router.py`
- [X] T017 [US1] Run `pytest tests/services/jdr/test_pjs.py -q` and confirm all US1 rename tests pass

**Checkpoint**: User Story 1 is fully functional and testable independently.

---

## Phase 4: User Story 2 - Link Or Unlink A User Account (Priority: P2)

**Goal**: A GM can set, change, or explicitly clear the `user_id` link on an owned PJ.

**Independent Test**: Create a PJ, PATCH it with a valid user UUID, confirm `PjOut.user_id`, then PATCH with `user_id: null` and confirm the link is cleared.

### Tests for User Story 2

- [X] T018 [P] [US2] Add a failing test for linking an existing user account to a PJ in `tests/services/jdr/test_pjs.py`
- [X] T019 [P] [US2] Add a failing test for clearing a linked user with explicit `user_id: null` in `tests/services/jdr/test_pjs.py`
- [X] T020 [P] [US2] Add a failing test that omitting `user_id` in a rename-only PATCH preserves the existing link in `tests/services/jdr/test_pjs.py`
- [X] T021 [P] [US2] Add a failing test that an unknown non-null `user_id` returns `422 invalid-user` on PATCH in `tests/services/jdr/test_pjs.py`
- [X] T022 [P] [US2] Add a failing test that an empty PATCH body `{}` is a no-op returning current `PjOut` in `tests/services/jdr/test_pjs.py`

### Implementation for User Story 2

- [X] T023 [US2] Implement field-presence handling for `user_id` using Pydantic provided fields or equivalent update data in `app/services/jdr/router.py`
- [X] T024 [US2] Validate non-null `user_id` against `core_users` before assignment in `logic.update_pj` in `app/services/jdr/logic.py`
- [X] T025 [US2] Assign valid `user_id` values and clear explicit `None` values in `logic.update_pj` in `app/services/jdr/logic.py`
- [X] T026 [US2] Map unknown update `user_id` failures from `PjAssignmentError` to `422 invalid-user` in `app/services/jdr/router.py`
- [X] T027 [US2] Run `pytest tests/services/jdr/test_pjs.py -q` and confirm all US2 link/unlink tests pass

**Checkpoint**: User Stories 1 and 2 both work independently.

---

## Phase 5: User Story 3 - Protect Character Ownership Boundaries (Priority: P3)

**Goal**: A GM cannot edit a PJ owned by another GM, and failed cross-owner attempts leave the original PJ unchanged.

**Independent Test**: Create one PJ for GM A, authenticate as GM B, PATCH GM A's PJ, confirm `404 pj-not-found`, then read/list as GM A and verify the PJ is unchanged.

### Tests for User Story 3

- [X] T028 [P] [US3] Add a failing test that PATCHing another GM's PJ returns `404 pj-not-found` in `tests/services/jdr/test_pjs.py`
- [X] T029 [P] [US3] Add a failing test that a failed cross-owner PATCH leaves the original PJ name and `user_id` unchanged in `tests/services/jdr/test_pjs.py`
- [X] T030 [P] [US3] Add a failing test that player-role or unauthenticated callers cannot PATCH PJs in `tests/services/jdr/test_pjs.py`

### Implementation for User Story 3

- [X] T031 [US3] Ensure `logic.update_pj` loads the target through owner/campaign-scoped PJ lookup in `app/services/jdr/logic.py`
- [X] T032 [US3] Map missing or foreign PJ updates to existing `404 pj-not-found` in `app/services/jdr/router.py`
- [X] T033 [US3] Confirm `PATCH /services/jdr/pjs/{pj_id}` uses the existing GM auth dependency in `app/services/jdr/router.py`
- [X] T034 [US3] Run `pytest tests/services/jdr/test_pjs.py -q` and confirm all US3 ownership tests pass

**Checkpoint**: All user stories are independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Regenerate contracts, update documentation, and run the project validation gates.

- [X] T035 [P] Add an OpenAPI test asserting `PATCH /services/jdr/pjs/{pj_id}` and `PjUpdate` are present in `tests/services/jdr/test_pjs.py`
- [X] T036 Regenerate `docs/context/api/openapi.json` from `app.main:app`
- [X] T037 [P] Update PJ endpoint documentation with PATCH behavior in `docs/services/jdr.md`
- [X] T038 [P] Update the PJ endpoint quick reference in `docs/memo.md`
- [X] T039 Add a BD-12 learning entry to `docs/journal.md`
- [X] T040 Run the quickstart contract check `rg '"/services/jdr/pjs/\\{pj_id\\}"|"PjUpdate"' docs/context/api/openapi.json`
- [X] T041 Run focused validation `pytest tests/services/jdr/test_pjs.py -q`
- [X] T042 Run full validation `ruff check .`, `pytest`, and `docker compose config --quiet`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; can start immediately.
- **Foundational (Phase 2)**: Depends on Setup; blocks all user stories.
- **User Story 1 (Phase 3)**: Depends on Foundational; MVP scope.
- **User Story 2 (Phase 4)**: Depends on Foundational and reuses the PATCH route introduced for US1.
- **User Story 3 (Phase 5)**: Depends on Foundational and should be verified after PATCH exists.
- **Polish (Phase 6)**: Depends on all selected user stories.

### User Story Dependencies

- **US1 Rename (P1)**: First deliverable; proves update endpoint exists.
- **US2 Link/Unlink (P2)**: Builds on the same endpoint and shared `PjUpdate` omitted-vs-null semantics.
- **US3 Ownership (P3)**: Uses the same route and validates security boundaries; can be tested once the route exists.

### Within Each User Story

- Write story tests first and confirm they fail for the expected reason.
- Implement logic before route mapping when the route depends on new business behavior.
- Run `pytest tests/services/jdr/test_pjs.py -q` at each checkpoint.
- Do not add DELETE, campaign moves, owner changes, or migrations in this feature.

### Parallel Opportunities

- T002, T003, and T004 can run in parallel during setup.
- T008 can be written while T005-T007 are being implemented, but should fail before the error mapping fix lands.
- US1 tests T010-T012 can be written in parallel.
- US2 tests T018-T022 can be written in parallel.
- US3 tests T028-T030 can be written in parallel.
- Documentation tasks T037 and T038 can run in parallel after behavior is stable.

---

## Parallel Example: User Story 1

```text
Task: "Add a failing test for successful PJ rename returning updated PjOut in tests/services/jdr/test_pjs.py"
Task: "Add a failing test that renaming one PJ does not modify another PJ owned by the same GM in tests/services/jdr/test_pjs.py"
Task: "Add a failing test that renaming to an existing same-GM PJ name returns 409 duplicate-pj in tests/services/jdr/test_pjs.py"
```

## Parallel Example: User Story 2

```text
Task: "Add a failing test for linking an existing user account to a PJ in tests/services/jdr/test_pjs.py"
Task: "Add a failing test for clearing a linked user with explicit user_id: null in tests/services/jdr/test_pjs.py"
Task: "Add a failing test that omitting user_id in a rename-only PATCH preserves the existing link in tests/services/jdr/test_pjs.py"
Task: "Add a failing test that an unknown non-null user_id returns 422 invalid-user on PATCH in tests/services/jdr/test_pjs.py"
```

## Parallel Example: User Story 3

```text
Task: "Add a failing test that PATCHing another GM's PJ returns 404 pj-not-found in tests/services/jdr/test_pjs.py"
Task: "Add a failing test that a failed cross-owner PATCH leaves the original PJ name and user_id unchanged in tests/services/jdr/test_pjs.py"
Task: "Add a failing test that player-role or unauthenticated callers cannot PATCH PJs in tests/services/jdr/test_pjs.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 and Phase 2.
2. Complete Phase 3 for rename support.
3. Validate with `pytest tests/services/jdr/test_pjs.py -q`.
4. Stop and review before adding link/unlink behavior if a smaller PR is desired.

### Incremental Delivery

1. Setup + Foundational -> shared schema and error contract.
2. US1 -> rename endpoint works.
3. US2 -> account link/unlink works without breaking rename.
4. US3 -> ownership and auth boundaries are proven.
5. Polish -> OpenAPI, docs, journal, full validation.

### Final Validation Gates

1. `ruff check .`
2. `pytest`
3. `docker compose config --quiet`
4. Manual or documented quickstart smoke test if the local stack is available.

## Notes

- `[P]` tasks touch different files or independent test cases and can be parallelized.
- `[US1]`, `[US2]`, and `[US3]` labels map directly to the stories in `spec.md`.
- Keep the implementation additive and scoped: no PJ deletion, no schema migration, no campaign ownership refactor.
