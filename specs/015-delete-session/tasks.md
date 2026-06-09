# Tasks: Delete JDR Session

**Input**: Design documents from `specs/015-delete-session/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/rest-api.md`, `quickstart.md`

**Tests**: Required by project constitution and BD-15 acceptance criteria. Write route/service tests first and verify they fail before implementation.

**Organization**: Tasks are grouped by user story so each story can be implemented and tested independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it touches different files or is independent from incomplete tasks.
- **[Story]**: Maps to the user story from `spec.md`.
- Every task includes an exact file path.

## Phase 1: Setup (Shared Context)

**Purpose**: Confirm existing session ownership, cascade, audio cleanup, and job-state behavior before coding.

- [X] T001 Review existing session route/auth behavior in `app/services/jdr/router.py`.
- [X] T002 [P] Review existing session repository relationships and cascade helpers in `app/services/jdr/db/models.py` and `app/services/jdr/db/repositories.py`.
- [X] T003 [P] Review existing audio purge behavior and active transcription conflict in `app/services/jdr/logic.py`.
- [X] T004 [P] Review existing session/audio/campaign tests for fixture patterns in `tests/services/jdr/test_sessions.py`, `tests/services/jdr/test_audio_purge.py`, and `tests/services/jdr/test_campaigns_crud.py`.
- [X] T005 [P] Review BD-15 contract in `specs/015-delete-session/contracts/rest-api.md`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add the domain error and deletion service skeleton shared by all stories.

**CRITICAL**: No user story implementation should begin until this phase is complete.

- [X] T006 Add `SessionDeleteBlockedError` domain exception in `app/services/jdr/logic.py`.
- [X] T007 Add `SessionDeleteBlockedError` HTTP AppError mapping in `app/services/jdr/router.py`.
- [X] T008 Add repository method to delete a `Session` aggregate in `app/services/jdr/db/repositories.py`.
- [X] T009 Add placeholder route test module `tests/services/jdr/test_sessions_delete.py` with reusable fixtures.

**Checkpoint**: Deletion has a test target and shared error vocabulary.

---

## Phase 3: User Story 1 - Delete Own Session (Priority: P1) MVP

**Goal**: A GM can delete their own non-active session and immediately see it disappear from direct reads, list reads, and campaign counts.

**Independent Test**: Create two sessions in one campaign, delete one, then verify `204`, direct `GET` returns `404`, list excludes it, and campaign count decreases.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation.**

- [X] T010 [P] [US1] Add route test for owned session delete returning `204` and subsequent `GET /sessions/{id}` returning `404` in `tests/services/jdr/test_sessions_delete.py`.
- [X] T011 [US1] Add route test proving deleted session is absent from `GET /services/jdr/sessions?campaign_id=...` in `tests/services/jdr/test_sessions_delete.py`.
- [X] T012 [US1] Add route test proving campaign `session_count` decreases after deletion in `tests/services/jdr/test_sessions_delete.py`.
- [X] T013 [US1] Run US1 tests and confirm they fail before implementation with `uv run pytest tests/services/jdr/test_sessions_delete.py -q`.

### Implementation for User Story 1

- [X] T014 [US1] Implement `delete_session` business operation with GM/campaign visibility checks in `app/services/jdr/logic.py`.
- [X] T015 [US1] Implement `DELETE /services/jdr/sessions/{session_id}` returning `204` in `app/services/jdr/router.py`.
- [X] T016 [US1] Ensure unknown or foreign sessions reuse `SessionNotFoundError` behavior in `app/services/jdr/router.py`.
- [X] T017 [US1] Run focused US1 validation with `uv run pytest tests/services/jdr/test_sessions_delete.py -q`.

**Checkpoint**: Session delete MVP works and preserves direct/list/campaign count behavior.

---

## Phase 4: User Story 2 - Remove Dependent Session Data (Priority: P2)

**Goal**: Deleting a session removes session-scoped database dependencies and stored audio without deleting reusable PJ rows.

**Independent Test**: Seed a session with audio, transcription, chunks, mapping, session players, artifacts, jobs, and edited transcript; delete it; verify no session-scoped rows or files remain.

### Tests for User Story 2

- [X] T018 [P] [US2] Add cascade test seeding audio file, audio source, transcription, chunks, mapping, session players, artifacts, jobs, and edited transcript in `tests/services/jdr/test_sessions_delete.py`.
- [X] T019 [US2] Add test proving missing audio file does not block deletion in `tests/services/jdr/test_sessions_delete.py`.
- [X] T020 [US2] Add test proving PJ rows survive while session mappings/players are removed in `tests/services/jdr/test_sessions_delete.py`.
- [X] T021 [US2] Run US2 tests and confirm they fail or protect cascade behavior before implementation with `uv run pytest tests/services/jdr/test_sessions_delete.py -q`.

### Implementation for User Story 2

- [X] T022 [US2] Add best-effort stored audio unlink and raw temp directory cleanup to `delete_session` in `app/services/jdr/logic.py`.
- [X] T023 [US2] Ensure session aggregate deletion removes owned DB relationships in `app/services/jdr/db/repositories.py`.
- [X] T024 [US2] Ensure deletion commits atomically after filesystem cleanup attempt in `app/services/jdr/logic.py`.
- [X] T025 [US2] Run focused US2 validation with `uv run pytest tests/services/jdr/test_sessions_delete.py -q`.

**Checkpoint**: Session-scoped dependency cleanup is verified.

---

## Phase 5: User Story 3 - Preserve Visibility And In-Flight Work Safety (Priority: P3)

**Goal**: Delete preserves auth/isolation rules and refuses sessions with active work.

**Independent Test**: Attempt delete with no auth, player auth, foreign GM, unknown id, transcribing session, and active current job; verify expected errors and unchanged rows.

### Tests for User Story 3

- [X] T026 [P] [US3] Add route test for unauthenticated delete returning `401` in `tests/services/jdr/test_sessions_delete.py`.
- [X] T027 [US3] Add route test for player credential delete returning `403` in `tests/services/jdr/test_sessions_delete.py`.
- [X] T028 [US3] Add route test for cross-GM delete returning `404 session-not-found` in `tests/services/jdr/test_sessions_delete.py`.
- [X] T029 [US3] Add route test for unknown session delete returning `404 session-not-found` in `tests/services/jdr/test_sessions_delete.py`.
- [X] T030 [US3] Add route test for `transcribing` session delete returning `409 session-delete-blocked` and preserving session in `tests/services/jdr/test_sessions_delete.py`.
- [X] T031 [US3] Add route tests for active RQ current job, generation enqueue conflict, and stale SQL/finished RQ delete behavior in `tests/services/jdr/test_sessions_delete.py`.
- [X] T032 [US3] Run US3 tests and confirm they fail or protect behavior before final implementation with `uv run pytest tests/services/jdr/test_sessions_delete.py -q`.

### Implementation for User Story 3

- [X] T033 [US3] Add active work guard for `SessionState.TRANSCRIBING` and observable active RQ current job in `app/services/jdr/logic.py`.
- [X] T034 [US3] Map active work guard to `409 session-delete-blocked` in `app/services/jdr/router.py`.
- [X] T035 [US3] Run focused US3 validation with `uv run pytest tests/services/jdr/test_sessions_delete.py -q`.

**Checkpoint**: Deletion is safe, scoped, and deterministic around active work.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, generated contracts, and final validation.

- [X] T036 [P] Regenerate OpenAPI into `docs/context/api/openapi.json`.
- [X] T037 [P] Update delete-session behavior in `docs/services/jdr.md`.
- [X] T038 [P] Add BD-15 command/reference row to `docs/memo.md`.
- [X] T039 [P] Add BD-15 learning entry to `docs/journal.md`.
- [X] T040 Run quickstart OpenAPI check from `specs/015-delete-session/quickstart.md`.
- [X] T041 Run focused delete validation command `uv run pytest tests/services/jdr/test_sessions_delete.py -q`.
- [X] T042 Run session regression validation `uv run pytest tests/services/jdr/test_sessions.py tests/services/jdr/test_campaigns_crud.py -q`.
- [X] T043 Run `uv run ruff check .` from repository root and fix reported issues.
- [X] T044 Run full `uv run pytest -q` from repository root and fix regressions.
- [X] T045 Run `docker compose config --quiet` from repository root and fix Compose validation issues.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: No dependencies.
- **Phase 2 Foundational**: Depends on Phase 1; blocks user stories.
- **Phase 3 US1**: Depends on Phase 2; MVP scope.
- **Phase 4 US2**: Depends on Phase 2 and benefits from US1 route existing.
- **Phase 5 US3**: Depends on Phase 2 and route skeleton from US1.
- **Phase 6 Polish**: Depends on all desired user stories.

### User Story Dependencies

- **US1 (P1)**: MVP. Provides the delete route and basic business operation.
- **US2 (P2)**: Extends delete operation with aggregate cleanup verification.
- **US3 (P3)**: Locks auth/isolation and active-work safety.

### Within Each User Story

- Write tests first and confirm failure or regression protection before implementation.
- Business logic before route mapping when both are needed.
- Route mapping before OpenAPI/docs.
- Story complete before moving to the next priority unless only tests are being added.

## Parallel Opportunities

- T002, T003, T004, and T005 can run in parallel with T001.
- T010 through T012 are conceptually independent but edit the same test file and should be applied sequentially in one worktree.
- T018 through T020 are conceptually independent but edit the same test file and should be applied sequentially.
- T026 through T031 are conceptually independent but edit the same test file and should be applied sequentially.
- T036 through T039 can run in parallel after implementation stabilizes.

## Parallel Example: User Story 1

```text
Task: "T010 [P] [US1] Add route test for owned session delete returning 204 in tests/services/jdr/test_sessions_delete.py"
Task: "T011 [US1] Add route test proving deleted session is absent from list in tests/services/jdr/test_sessions_delete.py"
Task: "T012 [US1] Add route test proving campaign session_count decreases in tests/services/jdr/test_sessions_delete.py"
```

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Setup and Foundational phases.
2. Add delete route tests for owned sessions.
3. Implement delete business operation and route.
4. Validate US1 independently.

### Incremental Delivery

1. US1: Delete own session and update visible reads/counts.
2. US2: Verify cascade and audio cleanup.
3. US3: Lock auth/isolation and active-work conflict.
4. Polish: OpenAPI, docs, memo, journal, full validation.

### Scope Guardrails

- Do not add soft delete or restore.
- Do not add bulk session deletion.
- Do not add job cancellation.
- Do not delete PJ rows, only session-scoped relationships.
- Do not change existing create/list/read/update session response contracts.
