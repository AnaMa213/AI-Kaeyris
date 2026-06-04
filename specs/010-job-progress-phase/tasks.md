# Tasks: JDR Job Progress Phase

**Input**: Design documents from `specs/010-job-progress-phase/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/rest-api.md`, `quickstart.md`
**Tests**: Required by project constitution and this feature plan. Write the focused tests before implementation.

**Organization**: Tasks are grouped by user story so each increment can be validated independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it touches different files or does not depend on incomplete tasks.
- **[Story]**: Required only in user-story phases.
- Every task includes at least one exact repository path.

---

## Phase 1: Setup (Shared Context)

**Purpose**: Confirm the current implementation points before writing failing tests.

- [X] T001 Review the existing job projection contract in `app/services/jdr/schemas.py` and `app/services/jdr/router.py`
- [X] T002 [P] Review current job route test fixtures in `tests/services/jdr/test_jobs_route.py`
- [X] T003 [P] Review transcription worker and chunking flow in `app/jobs/jdr.py`
- [X] T004 [P] Review BD-10 contract expectations in `specs/010-job-progress-phase/contracts/rest-api.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add shared contract primitives used by every story. No user story is complete until these are validated through its own tests.

**CRITICAL**: Complete this phase before implementing story-specific behavior.

- [X] T005 Add nullable `phase` and bounded nullable `progress_percent` fields to `JobOut` in `app/services/jdr/schemas.py`
- [X] T006 [P] Add or extend schema/OpenAPI assertions for `JobOut.phase` and `JobOut.progress_percent` in `tests/services/jdr/test_jobs_route.py`
- [X] T007 Run the schema-focused job route tests and confirm the new assertions fail before route/worker implementation using `tests/services/jdr/test_jobs_route.py`

**Checkpoint**: `JobOut` can represent the new public fields, but no runtime progress behavior is complete yet.

---

## Phase 3: User Story 1 - Voir l'avancement reel d'une transcription (Priority: P1) MVP

**Goal**: A running transcription job exposes a real phase and progress percent, then reaches `done` at 100 only after success.

**Independent Test**: Enqueue or fake a transcription job with RQ metadata and verify `GET /services/jdr/jobs/{job_id}` returns `phase="transcribing"` with `0..99`, then `phase="done"` with `100` for terminal success.

### Tests for User Story 1

- [X] T008 [P] [US1] Add route test for running transcription metadata `phase="transcribing"` and `0 <= progress_percent <= 99` in `tests/services/jdr/test_jobs_route.py`
- [X] T009 [P] [US1] Add route test for successful transcription metadata `phase="done"` and `progress_percent=100` in `tests/services/jdr/test_jobs_route.py`
- [X] T010 [P] [US1] Add chunk callback monotonicity test for `_transcribe_with_optional_chunking` in `tests/jobs/test_jdr_summary.py`
- [X] T011 [US1] Run US1 tests and confirm they fail before implementation using `tests/services/jdr/test_jobs_route.py` and `tests/jobs/test_jdr_summary.py`

### Implementation for User Story 1

- [X] T012 [US1] Add a local progress metadata emitter and current RQ job lookup around `transcribe_session_job` in `app/jobs/jdr.py`
- [X] T013 [US1] Add optional `on_progress` callback support to `_transcribe_with_optional_chunking` in `app/jobs/jdr.py`
- [X] T014 [US1] Emit `transcribing` progress from the chunk callback with `min(99, round(done / total * 100))` in `app/jobs/jdr.py`
- [X] T015 [US1] Emit terminal `phase="done"` and `progress_percent=100` after successful persistence and session state transition in `app/jobs/jdr.py`
- [X] T016 [US1] Read and validate RQ metadata in `get_job` and populate `JobOut.phase` and `JobOut.progress_percent` in `app/services/jdr/router.py`
- [X] T017 [US1] Run focused US1 validation with `tests/services/jdr/test_jobs_route.py` and `tests/jobs/test_jdr_summary.py`

**Checkpoint**: MVP complete. The frontend can consume real in-flight and terminal-success progress through the existing polling endpoint.

---

## Phase 4: User Story 2 - Conserver un etat fiable malgre les metadonnees absentes (Priority: P2)

**Goal**: Valid jobs remain readable when progress metadata is missing, expired, malformed, or not started.

**Independent Test**: Fetch a queued or metadata-free job and verify HTTP 200 with `phase=null`, `progress_percent=null`, and unchanged main `status`.

### Tests for User Story 2

- [X] T018 [P] [US2] Extend the freshly queued job test to assert `phase is None` and `progress_percent is None` in `tests/services/jdr/test_jobs_route.py`
- [X] T019 [P] [US2] Add route test for malformed or out-of-domain progress metadata falling back to null fields in `tests/services/jdr/test_jobs_route.py`
- [X] T020 [US2] Run US2 tests and confirm fallback assertions fail before implementation using `tests/services/jdr/test_jobs_route.py`

### Implementation for User Story 2

- [X] T021 [US2] Harden metadata extraction so missing, malformed, non-integer, or out-of-range progress values return nullable fields in `app/services/jdr/router.py`
- [X] T022 [US2] Ensure queued jobs do not synthesize `phase="queued"` or `progress_percent=0` in `app/services/jdr/router.py`
- [X] T023 [US2] Run focused US2 validation with `tests/services/jdr/test_jobs_route.py`

**Checkpoint**: Job polling stays backward-compatible and best-effort progress never breaks a valid job response.

---

## Phase 5: User Story 3 - Comprendre un echec sans perdre le dernier avancement connu (Priority: P3)

**Goal**: A failed transcription job exposes `phase="failed"` when available and keeps the last known progress value instead of resetting to 0.

**Independent Test**: Simulate a transcription job that fails after progress metadata exists and verify `status="failed"`, `phase="failed"`, and unchanged last `progress_percent`.

### Tests for User Story 3

- [X] T024 [P] [US3] Add route test for failed transcription metadata preserving the last progress percent in `tests/services/jdr/test_jobs_route.py`
- [X] T025 [P] [US3] Add worker test for failure progress emission preserving the previous percent in `tests/jobs/test_jdr_summary.py`
- [X] T026 [US3] Run US3 tests and confirm failure-progress assertions fail before implementation using `tests/services/jdr/test_jobs_route.py` and `tests/jobs/test_jdr_summary.py`

### Implementation for User Story 3

- [X] T027 [US3] Emit `phase="failed"` while preserving the last known `progress_percent` in transcription exception paths in `app/jobs/jdr.py`
- [X] T028 [US3] Ensure `get_job` returns failed progress metadata alongside the existing `failure_reason` projection in `app/services/jdr/router.py`
- [X] T029 [US3] Run focused US3 validation with `tests/services/jdr/test_jobs_route.py` and `tests/jobs/test_jdr_summary.py`

**Checkpoint**: Failed transcription progress is diagnostic and does not erase useful last-known progress.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Contract sync, documentation, and full Definition-of-Done checks.

- [X] T030 [P] Regenerate or create the public OpenAPI artifact containing `JobOut.phase` and `JobOut.progress_percent` in `docs/context/api/openapi.json`
- [X] T031 [P] Document the enriched JDR job polling contract in `docs/services/jdr.md`
- [X] T032 [P] Add the BD-10 quick reference entry for job progress fields in `docs/memo.md`
- [X] T033 Add the implementation learning entry for BD-10 in `docs/journal.md`
- [X] T034 Run the quickstart contract check for `phase` and `progress_percent` against `docs/context/api/openapi.json`
- [X] T035 Run focused validation commands from `specs/010-job-progress-phase/quickstart.md`
- [X] T036 Run full quality validation covering `app/main.py`, `tests/services/jdr/test_jobs_route.py`, and `pyproject.toml` with `ruff check .` and `pytest`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup and blocks every user story.
- **US1 (Phase 3)**: Depends on Foundational and is the MVP.
- **US2 (Phase 4)**: Depends on Foundational; safest after US1 because it hardens the same route fields.
- **US3 (Phase 5)**: Depends on US1 worker emission and route metadata projection.
- **Polish (Phase 6)**: Depends on selected user stories being complete.

### User Story Dependencies

- **US1**: Provides the base runtime progress path and can be delivered first.
- **US2**: Can be tested independently with metadata-free jobs, but shares the `JobOut` fields from Foundational and the route extraction area from US1.
- **US3**: Depends on the worker progress emitter introduced in US1.

### Within Each User Story

- Write and run story tests first.
- Implement worker behavior before route assertions that require emitted metadata.
- Keep RQ-specific code at the job boundary in `app/jobs/jdr.py`.
- Keep public contract projection in `app/services/jdr/router.py` and `app/services/jdr/schemas.py`.

---

## Parallel Opportunities

- T002, T003, and T004 can run in parallel during setup.
- T006 can run in parallel with T005 once the expected schema shape is agreed.
- T008, T009, and T010 can be written in parallel because they cover different assertions and files.
- T018 and T019 can be written in parallel.
- T024 and T025 can be written in parallel.
- T030, T031, and T032 can run in parallel after implementation is complete.

---

## Parallel Example: User Story 1

```text
Task: "T008 [P] [US1] Add route test for running transcription metadata in tests/services/jdr/test_jobs_route.py"
Task: "T009 [P] [US1] Add route test for successful transcription metadata in tests/services/jdr/test_jobs_route.py"
Task: "T010 [P] [US1] Add chunk callback monotonicity test in tests/jobs/test_jdr_summary.py"
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1 and Phase 2.
2. Complete US1 tests and implementation.
3. Validate focused route and worker tests.
4. Stop and demo: existing polling endpoint shows real running progress and terminal success.

### Incremental Delivery

1. US1 adds real visible progress.
2. US2 hardens absent/malformed metadata fallback.
3. US3 improves failed-job diagnostics.
4. Polish regenerates contract and docs.

### Scope Guardrails

- Do not add SSE, WebSocket, pub/sub, or progress database columns in this feature.
- Do not add `queued` to the phase enum.
- Do not let `phase` drive completion; `status` remains authoritative.
- Do not reset failed progress to 0 when a last value exists.
