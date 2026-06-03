# Tasks: Current Job and Audio Stream

**Input**: Design documents from `specs/008-current-job-audio-stream/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/rest-api.md, quickstart.md

**Tests**: Required by project constitution and feature quickstart. Write/adjust tests before implementation tasks for each story.

**Organization**: Tasks are grouped by user story so each increment can be implemented and validated independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel with other [P] tasks in the same phase because it touches different files or only reads context
- **[Story]**: User story label for traceability
- Every task includes exact target file paths

## Phase 1: Setup (Shared Context)

**Purpose**: Confirm current implementation points and avoid accidental scope expansion.

- [X] T001 Inspect current session/audio/job code paths in `app/services/jdr/db/models.py`, `app/services/jdr/schemas.py`, `app/services/jdr/db/repositories.py`, `app/services/jdr/logic.py`, `app/services/jdr/router.py`, `app/services/jdr/batch/router.py`, and `app/jobs/jdr.py`
- [X] T002 [P] Inspect existing audio/session/job tests in `tests/services/jdr/test_audio_upload.py`, `tests/services/jdr/test_audio_purge.py`, `tests/services/jdr/test_sessions.py`, `tests/services/jdr/test_jobs_route.py`, and `tests/jobs/test_transcription_flow.py`
- [X] T003 [P] Inspect migration conventions in `migrations/versions/` and identify the current Alembic head revision

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add the shared data contract required by every story.

**Critical**: No user story work can be complete until `Session.current_job_id` exists end-to-end.

- [X] T004 Add failing schema/model tests for nullable `current_job_id` on session outputs in `tests/services/jdr/test_sessions.py`
- [X] T005 Add nullable `current_job_id` field and relationship metadata to `Session` in `app/services/jdr/db/models.py`
- [X] T006 Add an Alembic migration adding nullable `current_job_id` with `ON DELETE SET NULL` in `migrations/versions/`
- [X] T007 Add `current_job_id` to `SessionOut` in `app/services/jdr/schemas.py`
- [X] T008 Update session response mapping if needed so `POST /sessions`, `GET /sessions`, and `GET /sessions/{session_id}` serialize `current_job_id` in `app/services/jdr/router.py`
- [X] T009 Run the focused session output tests in `tests/services/jdr/test_sessions.py`

**Checkpoint**: Session outputs can expose a nullable current transcription job pointer without changing existing required fields.

---

## Phase 3: User Story 1 - Resume Transcription Tracking After Refresh (Priority: P1) MVP

**Goal**: A refreshed session detail can recover the active or latest transcription job id from server state alone.

**Independent Test**: Upload audio, fetch session detail, verify `current_job_id` equals the upload `job_id`; complete/fail transcription and verify the pointer remains until purge.

### Tests for User Story 1

- [X] T010 [P] [US1] Add upload test proving `POST /services/jdr/sessions/{session_id}/audio` sets later `SessionOut.current_job_id` in `tests/services/jdr/test_audio_upload.py`
- [X] T011 [P] [US1] Add transcription success test proving `current_job_id` remains after terminal `transcribed` state in `tests/services/jdr/test_transcription_flow.py`
- [X] T012 [P] [US1] Add transcription failure test proving `current_job_id` remains after `transcription_failed` in `tests/services/jdr/test_transcription_flow.py`

### Implementation for User Story 1

- [X] T013 [US1] Update `SessionRepository` with a helper to set and clear session `current_job_id` in `app/services/jdr/db/repositories.py`
- [X] T014 [US1] Update `store_audio_source_for_session` to persist the enqueued transcription job id on the session in `app/services/jdr/logic.py`
- [X] T015 [US1] Update transcription job success and failure paths to preserve `current_job_id` in `app/jobs/jdr.py`
- [X] T016 [US1] Run focused tests for upload and transcription flow in `tests/services/jdr/test_audio_upload.py` and `tests/services/jdr/test_transcription_flow.py`

**Checkpoint**: User Story 1 works independently and unblocks frontend polling recovery.

---

## Phase 4: User Story 2 - Play and Seek Session Audio (Priority: P1)

**Goal**: Authorized campaign users can retrieve session audio, and browser players can seek with byte ranges.

**Independent Test**: Request full audio and a valid byte range for a session with audio; verify playable headers and 206 range behavior.

### Tests for User Story 2

- [X] T017 [P] [US2] Add full audio retrieval tests for authorized session members in `tests/services/jdr/test_audio_get.py`
- [X] T018 [P] [US2] Add range request tests for `206`, `Content-Range`, `Content-Length`, and `Accept-Ranges` in `tests/services/jdr/test_audio_get.py`
- [X] T019 [P] [US2] Add not-found and cross-campaign isolation tests for audio retrieval in `tests/services/jdr/test_audio_get.py`
- [X] T020 [P] [US2] Add invalid range test for `416` behavior in `tests/services/jdr/test_audio_get.py`

### Implementation for User Story 2

- [X] T021 [US2] Add audio lookup/read metadata helper for non-purged audio in `app/services/jdr/logic.py`
- [X] T022 [US2] Add `GET /sessions/{session_id}/audio` route with auth, campaign scoping, full-file response, and private cache headers in `app/services/jdr/batch/router.py`
- [X] T023 [US2] Add byte-range parsing and partial response handling in `app/services/jdr/batch/router.py`
- [X] T024 [US2] Add route-level Problem Details mapping for missing audio, missing file, unauthorized access, and invalid range in `app/services/jdr/batch/router.py`
- [X] T025 [US2] Update transcription success behavior to keep source audio unpurged and present on disk in `app/jobs/jdr.py`
- [X] T026 [US2] Update existing transcription-flow assertions that currently expect audio auto-purge in `tests/services/jdr/test_transcription_flow.py` and `tests/services/jdr/test_transcription_flow_non_diarised.py`
- [X] T027 [US2] Run focused audio retrieval and transcription-flow tests in `tests/services/jdr/test_audio_get.py`, `tests/services/jdr/test_transcription_flow.py`, and `tests/services/jdr/test_transcription_flow_non_diarised.py`

**Checkpoint**: User Story 2 works independently for sessions with stored audio and does not break terminal transcription states.

---

## Phase 5: User Story 3 - Replace Audio Deliberately and Irreversibly (Priority: P2)

**Goal**: GM deletion fully resets replaceable sessions and refuses active transcription without corrupting state.

**Independent Test**: Delete audio from `created`, `audio_uploaded`, `transcription_failed`, and `transcribed` sessions and verify 204/reset; delete during `transcribing` and verify 409/no mutation.

### Tests for User Story 3

- [X] T028 [P] [US3] Update purge tests for idempotent `created` delete returning 204 in `tests/services/jdr/test_audio_purge.py`
- [X] T029 [P] [US3] Update purge tests to allow `transcribed` delete returning 204 with reset to `created` in `tests/services/jdr/test_audio_purge.py`
- [X] T030 [P] [US3] Add purge tests proving transcription, chunks, artifacts, and `current_job_id` are cleared in `tests/services/jdr/test_audio_purge.py`
- [X] T031 [P] [US3] Add purge test proving `transcribing` still returns 409 and preserves audio/state/job pointer in `tests/services/jdr/test_audio_purge.py`

### Implementation for User Story 3

- [X] T032 [US3] Update `purge_audio_for_session` to allow `created`, `audio_uploaded`, `transcription_failed`, and `transcribed` while refusing only `transcribing` in `app/services/jdr/logic.py`
- [X] T033 [US3] Add repository helpers to clear transcription rows, chunks, derived artifacts, audio purge marker, and `current_job_id` in `app/services/jdr/db/repositories.py`
- [X] T034 [US3] Update DELETE route error mapping so no-audio/created idempotent deletes return 204 and missing/foreign sessions remain 404 in `app/services/jdr/batch/router.py`
- [X] T035 [US3] Ensure failed transcription preserves source audio for retry without re-upload in `app/jobs/jdr.py`
- [X] T036 [US3] Run focused purge and job-failure tests in `tests/services/jdr/test_audio_purge.py` and `tests/services/jdr/test_transcription_flow.py`

**Checkpoint**: User Story 3 supports deliberate replacement and keeps active transcription protected.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, generated contracts, and full quality gate.

- [X] T037 [P] Verify generated OpenAPI snapshot/API type contract source is not tracked at `docs/context/api/openapi.json`
- [X] T038 [P] Update user-facing backend behavior notes in `README.md`
- [X] T039 [P] Add a journal entry for BD-8 decisions in `docs/journal.md`
- [X] T040 Run `uv run ruff check .` for repository root `.`
- [X] T041 Run `uv run pytest tests/services/jdr -q`
- [X] T042 Run `uv run pytest tests/jobs -q`
- [X] T043 Run quickstart verification steps from `specs/008-current-job-audio-stream/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: No dependencies.
- **Phase 2 Foundational**: Depends on setup inspection; blocks all stories.
- **Phase 3 US1**: Depends on Phase 2.
- **Phase 4 US2**: Depends on Phase 2; can start in parallel with US1 after foundation, but final behavior expects US1 job pointer semantics.
- **Phase 5 US3**: Depends on Phase 2; safest after US1/US2 because it updates purge behavior used by both.
- **Phase 6 Polish**: Depends on selected user stories being complete.

### User Story Dependencies

- **US1 (P1)**: MVP. Required to unblock frontend polling recovery.
- **US2 (P1)**: Independent playback increment once audio is preserved; benefits from US1 only for shared lifecycle expectations.
- **US3 (P2)**: Replacement/reset semantics; should follow US1/US2 to avoid undoing preservation and pointer behavior.

### Within Each User Story

- Tests first, confirm failures.
- Model/schema/repository before logic.
- Logic before route integration.
- Route integration before quickstart/manual validation.

---

## Parallel Opportunities

- T002 and T003 can run while T001 inspects code paths.
- T010, T011, and T012 can be written in parallel.
- T017, T018, T019, and T020 can be written in parallel in the same new test file if coordinated, or sequentially by one implementer.
- T028, T029, T030, and T031 can be written in parallel if test fixtures are stable.
- T037, T038, and T039 can run in parallel after implementation.

## Parallel Example: User Story 1

```text
Task: "T010 [US1] Add upload test proving current_job_id is set in tests/services/jdr/test_audio_upload.py"
Task: "T011 [US1] Add transcription success pointer preservation test in tests/jobs/test_transcription_flow.py"
Task: "T012 [US1] Add transcription failure pointer preservation test in tests/jobs/test_transcription_flow.py"
```

## Parallel Example: User Story 2

```text
Task: "T017 [US2] Add full audio retrieval tests in tests/services/jdr/test_audio_get.py"
Task: "T018 [US2] Add range request tests in tests/services/jdr/test_audio_get.py"
Task: "T019 [US2] Add not-found and cross-campaign isolation tests in tests/services/jdr/test_audio_get.py"
Task: "T020 [US2] Add invalid range test in tests/services/jdr/test_audio_get.py"
```

## Parallel Example: User Story 3

```text
Task: "T028 [US3] Update idempotent created delete tests in tests/services/jdr/test_audio_purge.py"
Task: "T029 [US3] Update transcribed delete reset tests in tests/services/jdr/test_audio_purge.py"
Task: "T030 [US3] Add derived-data cleanup tests in tests/services/jdr/test_audio_purge.py"
Task: "T031 [US3] Add transcribing preservation test in tests/services/jdr/test_audio_purge.py"
```

---

## Implementation Strategy

### MVP First (US1)

1. Complete Phase 1 and Phase 2.
2. Complete Phase 3 only.
3. Validate upload/session detail/job terminal pointer behavior.
4. Stop for review if frontend Story 3.4 is the immediate blocker.

### Incremental Delivery

1. US1: Resume polling after refresh.
2. US2: Add playable/seekable audio.
3. US3: Add deliberate destructive replacement.
4. Polish: docs, OpenAPI snapshot, full quality gate.

### Risk Notes

- Existing tests expect successful transcription to purge audio; update those expectations deliberately, not by weakening assertions.
- DELETE semantics intentionally change from "404/409 for no audio/transcribed" toward "204 idempotent except transcribing"; keep missing or foreign sessions as 404.
- Do not implement Epic 4 multi-job listing or a signed URL flow in this story.
