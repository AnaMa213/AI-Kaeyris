# Tasks: Live Job Events

**Input**: Design documents from `specs/014-sse-artifact-jobs/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/rest-api.md`, `quickstart.md`

**Tests**: Required by project constitution and BD-14 acceptance criteria. Write route tests first and verify they fail before implementation.

**Organization**: Tasks are grouped by user story so each story can be implemented and tested independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it touches different files or is independent from incomplete tasks.
- **[Story]**: Maps to the user story from `spec.md`.
- Every task includes an exact file path.

## Phase 1: Setup (Shared Context)

**Purpose**: Confirm the current job status surface and prepare the test target.

- [X] T001 Review existing `GET /services/jdr/jobs/{job_id}` projection helpers and auth flow in `app/services/jdr/router.py`.
- [X] T002 [P] Review existing job route fixtures and helper patterns in `tests/services/jdr/test_jobs_route.py`.
- [X] T003 [P] Review BD-14 event contract in `specs/014-sse-artifact-jobs/contracts/rest-api.md`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Extract shared projection/serialization so polling and SSE cannot drift.

**CRITICAL**: No user story implementation should begin until this phase is complete.

- [X] T004 Refactor `get_job` logic into a private shared projection helper returning `JobOut` in `app/services/jdr/router.py`.
- [X] T005 Keep `GET /services/jdr/jobs/{job_id}` behavior unchanged by delegating to the shared projection helper in `app/services/jdr/router.py`.
- [X] T006 Add a private SSE payload serializer for `status`, `phase`, `progress_percent`, and optional `failure_reason` in `app/services/jdr/router.py`.
- [X] T007 Add a private SSE frame formatter that emits `event: progress` and JSON `data` frames in `app/services/jdr/router.py`.
- [X] T008 Run current polling route tests to confirm the refactor preserves behavior with `uv run pytest tests/services/jdr/test_jobs_route.py -q`.

**Checkpoint**: Polling is still green and shared projection helpers are ready for live events.

---

## Phase 3: User Story 1 - Follow Artifact Jobs Live (Priority: P1) MVP

**Goal**: A GM can subscribe to a running artifact job, receive status updates, receive the terminal success or failure event, and see the stream close.

**Independent Test**: Simulate a visible artifact job in Redis, subscribe to `/services/jdr/jobs/{job_id}/events`, update the job to `succeeded` or `failed`, and assert received `progress` frames.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation.**

- [X] T009 [P] [US1] Add route test for a running artifact job stream emitting `status="running"` then terminal `status="succeeded"` in `tests/services/jdr/test_jobs_route.py`.
- [X] T010 [US1] Add route test for a failed artifact job stream including `failure_reason` before closing in `tests/services/jdr/test_jobs_route.py`.
- [X] T011 [US1] Add route test for a job already `succeeded` before subscription emitting one terminal frame and closing in `tests/services/jdr/test_jobs_route.py`.
- [X] T012 [US1] Run the US1 SSE tests and confirm they fail before endpoint implementation with `uv run pytest tests/services/jdr/test_jobs_route.py -q`.

### Implementation for User Story 1

- [X] T013 [US1] Add async live job event generator that reuses the shared projection helper and stops on `succeeded` or `failed` in `app/services/jdr/router.py`.
- [X] T014 [US1] Add `GET /services/jdr/jobs/{job_id}/events` returning `text/event-stream` with `Cache-Control: no-cache` in `app/services/jdr/router.py`.
- [X] T015 [US1] Document the SSE response media type and event format in the route metadata for OpenAPI in `app/services/jdr/router.py`.
- [X] T016 [US1] Run focused US1 validation with `uv run pytest tests/services/jdr/test_jobs_route.py -q`.

**Checkpoint**: Artifact jobs are live-trackable independently and polling still works.

---

## Phase 4: User Story 2 - Use One Live Tracking Model For All Jobs (Priority: P2)

**Goal**: The same live event endpoint works for transcription jobs and preserves BD-10 phase/progress metadata when present.

**Independent Test**: Simulate a transcription job with `phase="transcribing"` and `progress_percent=42`, subscribe to the same live endpoint, and verify the streamed payload matches the polling progress fields.

### Tests for User Story 2

- [X] T017 [P] [US2] Add route test for a transcription job stream preserving `phase` and `progress_percent` in `tests/services/jdr/test_jobs_route.py`.
- [X] T018 [US2] Add route test proving an artifact job stream keeps `phase` and `progress_percent` as null in `tests/services/jdr/test_jobs_route.py`.
- [X] T019 [US2] Run the US2 tests and confirm they fail if the stream bypasses `JobOut` projection with `uv run pytest tests/services/jdr/test_jobs_route.py -q`.

### Implementation for User Story 2

- [X] T020 [US2] Ensure SSE event generation serializes `phase` and `progress_percent` from `JobOut` for all job kinds in `app/services/jdr/router.py`.
- [X] T021 [US2] Ensure the SSE generator does not synthesize artifact phases or percent values in `app/services/jdr/router.py`.
- [X] T022 [US2] Run focused US2 validation with `uv run pytest tests/services/jdr/test_jobs_route.py -q`.

**Checkpoint**: One live tracking model covers transcription and artifact jobs.

---

## Phase 5: User Story 3 - Preserve Polling Fallback And Visibility Rules (Priority: P3)

**Goal**: Existing polling remains unchanged, and live events preserve authentication, role, not-found, and cross-tenant behavior.

**Independent Test**: Fetch the same known job through polling and live events; then try missing auth, player auth, unknown job, and foreign GM access against the live endpoint.

### Tests for User Story 3

- [X] T023 [P] [US3] Add regression test proving `GET /services/jdr/jobs/{job_id}` still returns unchanged `JobOut` after SSE support in `tests/services/jdr/test_jobs_route.py`.
- [X] T024 [US3] Add route test for unauthenticated live events returning 401 in `tests/services/jdr/test_jobs_route.py`.
- [X] T025 [US3] Add route test for player credentials rejected from live events with 403 in `tests/services/jdr/test_jobs_route.py`.
- [X] T026 [US3] Add route test for unknown live event job returning `job-not-found` in `tests/services/jdr/test_jobs_route.py`.
- [X] T027 [US3] Add route test for cross-tenant live event job returning 404 without leaking details in `tests/services/jdr/test_jobs_route.py`.
- [X] T028 [US3] Run the US3 tests and confirm they fail or protect existing behavior before final implementation with `uv run pytest tests/services/jdr/test_jobs_route.py -q`.

### Implementation for User Story 3

- [X] T029 [US3] Ensure `GET /services/jdr/jobs/{job_id}/events` uses the same `require_gm`, DB, Redis, and visibility logic as polling in `app/services/jdr/router.py`.
- [X] T030 [US3] Ensure unknown or foreign jobs raise the existing `JobNotFoundError` before starting a stream in `app/services/jdr/router.py`.
- [X] T031 [US3] Run focused US3 validation with `uv run pytest tests/services/jdr/test_jobs_route.py -q`.

**Checkpoint**: Polling fallback and job isolation are preserved.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, generated contracts, and final validation.

- [X] T032 [P] Regenerate OpenAPI into `docs/context/api/openapi.json`.
- [X] T033 [P] Update live job events and fallback behavior in `docs/services/jdr.md`.
- [X] T034 [P] Add BD-14 command/reference row to `docs/memo.md`.
- [X] T035 [P] Add BD-14 learning entry to `docs/journal.md`.
- [X] T036 Run quickstart OpenAPI check from `specs/014-sse-artifact-jobs/quickstart.md`.
- [X] T037 Run focused validation command `uv run pytest tests/services/jdr/test_jobs_route.py -q`.
- [X] T038 Run `uv run ruff check .` from repository root and fix reported issues.
- [X] T039 Run full `uv run pytest -q` from repository root and fix regressions.
- [X] T040 Run `docker compose config --quiet` from repository root and fix Compose validation issues.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: No dependencies.
- **Phase 2 Foundational**: Depends on Phase 1; blocks all user stories.
- **Phase 3 US1**: Depends on Phase 2; MVP scope.
- **Phase 4 US2**: Depends on Phase 2; can be developed after or alongside US1 once the endpoint skeleton exists.
- **Phase 5 US3**: Depends on Phase 2; best after US1 so the endpoint exists.
- **Phase 6 Polish**: Depends on completed desired user stories.

### User Story Dependencies

- **US1 (P1)**: MVP. Requires only the shared projection helpers from Phase 2.
- **US2 (P2)**: Builds on the same SSE endpoint and verifies generic metadata behavior.
- **US3 (P3)**: Builds on the same SSE endpoint and locks fallback/security behavior.

### Within Each User Story

- Write tests first and confirm they fail before implementation.
- Shared projection before event serialization.
- Event serialization before endpoint route.
- Endpoint route before OpenAPI/docs.

## Parallel Opportunities

- T002 and T003 can run in parallel with T001.
- T009, T010, and T011 are conceptually independent test cases, but they edit the same file and should be applied sequentially in one worktree.
- T017 and T018 are conceptually independent test cases, but they edit the same file and should be applied sequentially in one worktree.
- T024 through T027 are conceptually independent test cases, but they edit the same file and should be applied sequentially in one worktree.
- T032 through T035 can run in parallel after implementation stabilizes.

## Parallel Example: User Story 1

```text
Task: "T009 [P] [US1] Add route test for running artifact job stream in tests/services/jdr/test_jobs_route.py"
Task: "T010 [P] [US1] Add route test for failed artifact job stream in tests/services/jdr/test_jobs_route.py"
Task: "T011 [P] [US1] Add route test for already-terminal artifact job stream in tests/services/jdr/test_jobs_route.py"
```

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Setup and Foundational phases.
2. Add the artifact-job live event tests.
3. Implement the shared projection helper, SSE serializer, generator, and route.
4. Validate US1 with `uv run pytest tests/services/jdr/test_jobs_route.py -q`.

### Incremental Delivery

1. US1: Artifact jobs stream running and terminal events.
2. US2: Same stream supports transcription metadata and artifact null progress.
3. US3: Polling fallback and visibility rules are locked.
4. Polish: OpenAPI, docs, memo, journal, full validation.

### Scope Guardrails

- Do not add database migrations.
- Do not add WebSocket support.
- Do not add Redis pub/sub or event history.
- Do not synthesize artifact progress percentages.
- Do not change the existing `GET /jobs/{job_id}` JSON contract.
