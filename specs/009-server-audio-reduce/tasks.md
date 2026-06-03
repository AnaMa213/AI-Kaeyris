# Tasks: Server Audio Reduce

**Input**: Design documents from `specs/009-server-audio-reduce/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/rest-api.md, quickstart.md

**Tests**: Included because the project constitution requires endpoint tests and non-trivial worker/file lifecycle tests. Write the tests first and verify they fail before implementing.

**Organization**: Tasks are grouped by user story so each story can be implemented and validated as an independent increment.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel with other marked tasks in the same phase because it touches different files or only adds tests/docs.
- **[Story]**: User story label from `spec.md`; used only in user story phases.
- Every task includes an exact repository path.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Prepare shared configuration and docs references used by all BD-9 work.

- [X] T001 Add `KAEYRIS_AUDIO_MAX_UPLOAD_BYTES` defaulting to `524288000` with positive integer validation in `app/core/config.py`
- [X] T002 [P] Document `KAEYRIS_AUDIO_MAX_UPLOAD_BYTES` in `.env.example`
- [X] T003 [P] Add a concise BD-9 command/reference row for the upload limit and server-side audio preparation in `docs/memo.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build shared audio preparation primitives and repository operations needed before any user story can be completed.

**CRITICAL**: No user story implementation should begin until this phase is complete.

- [X] T004 [P] Add failing unit tests for server-side audio preparation success, ffmpeg missing, ffmpeg non-zero exit, and empty output in `tests/services/jdr/test_audio_reduce.py`
- [X] T005 Implement `PreparedAudioResult`, `AudioReduceError`, and `prepare_audio_for_transcription()` in `app/services/jdr/audio.py`
- [X] T006 Add failing repository tests for updating canonical audio metadata and path in `tests/services/jdr/test_audio_repository.py`
- [X] T007 Implement `SessionRepository.update_audio_source_file()` to update `path`, `sha256`, `size_bytes`, and `duration_seconds` in `app/services/jdr/db/repositories.py`
- [X] T008 Add shared filesystem helper functions for raw transient path, prepared audio path, checksum, and best-effort cleanup in `app/services/jdr/logic.py`

**Checkpoint**: Audio can be prepared into a durable artifact and the canonical `AudioSource` row can be updated without changing public behavior yet.

---

## Phase 3: User Story 1 - Upload Long Session Audio Without Client Reduction (Priority: P1) MVP

**Goal**: A GM can upload raw session audio within the supported limit, receive `202`, and have the backend prepare it in the existing processing pipeline before transcription.

**Independent Test**: Upload raw audio under the configured limit, run the transcription job core with audio preparation patched to a deterministic output, and verify the session enters/transitions through the normal transcription flow with one job reference.

### Tests for User Story 1

- [X] T009 [P] [US1] Add failing upload test for accepting raw audio under `KAEYRIS_AUDIO_MAX_UPLOAD_BYTES` and storing it as transient raw input in `tests/services/jdr/test_audio_upload.py`
- [X] T010 [P] [US1] Add failing upload test for rejecting an oversized raw audio upload with HTTP 413 and no orphan audio source/file in `tests/services/jdr/test_audio_upload.py`
- [X] T011 [P] [US1] Add failing worker test for successful preparation before transcription and raw deletion after preparation in `tests/jobs/test_transcribe_audio_reduce.py`
- [X] T012 [P] [US1] Add failing worker test for permanent preparation failure marking the session failed before transcription starts in `tests/jobs/test_transcribe_audio_reduce.py`

### Implementation for User Story 1

- [X] T013 [US1] Add `AudioUploadTooLargeError` and enforce `settings.KAEYRIS_AUDIO_MAX_UPLOAD_BYTES` while streaming upload chunks in `app/services/jdr/logic.py`
- [X] T014 [US1] Store accepted raw uploads under `KAEYRIS_DATA_DIR/.tmp/audio-reduce/<session_id>/raw.m4a` while still creating the existing `AudioSource` and transcription job in `app/services/jdr/logic.py`
- [X] T015 [US1] Map `AudioUploadTooLargeError` to HTTP 413 Problem Details including the effective limit in `app/services/jdr/batch/router.py`
- [X] T016 [US1] Call `prepare_audio_for_transcription()` at the start of `_transcribe_session`, update the `AudioSource` canonical metadata, delete raw input after success, and pass the prepared path to transcription in `app/jobs/jdr.py`
- [X] T017 [US1] Mark session/job failure consistently when audio preparation fails permanently in `app/jobs/jdr.py`
- [X] T018 [US1] Ensure transient raw files are best-effort removed after oversized upload and preparation failure paths in `app/services/jdr/logic.py` and `app/jobs/jdr.py`

**Checkpoint**: User Story 1 is fully functional and testable independently as the MVP.

---

## Phase 4: User Story 2 - Keep the Existing Frontend Contract Stable (Priority: P1)

**Goal**: The frontend continues using the same upload response, session state lifecycle, current job pointer, and job kind.

**Independent Test**: Submit audio through the existing upload route and verify the response shape, `current_job_id`, session states, and job kind remain compatible with BD-8 expectations.

### Tests for User Story 2

- [X] T019 [P] [US2] Add failing API contract regression test that `POST /services/jdr/sessions/{session_id}/audio` still returns `AudioUploadOut` with `job_id` in `tests/services/jdr/test_audio_upload.py`
- [X] T020 [P] [US2] Add failing regression test that upload-created job kind remains `transcription` and `SessionOut.current_job_id` points to it in `tests/services/jdr/test_audio_upload.py`
- [X] T021 [P] [US2] Add failing worker/session test proving no `reducing` state or `audio_reduce` job kind is exposed during preparation in `tests/jobs/test_transcribe_audio_reduce.py`
- [X] T022 [P] [US2] Add failing 413 contract test that the effective limit is visible in the Problem Details response in `tests/services/jdr/test_audio_upload.py`

### Implementation for User Story 2

- [X] T023 [US2] Preserve the existing `AudioUploadOut` schema and response mapping while updating any path/size semantics in `app/services/jdr/schemas.py` and `app/services/jdr/batch/router.py`
- [X] T024 [US2] Ensure `JobRepository.upsert_status()` continues to use `JobKind.TRANSCRIPTION` for upload-created processing in `app/services/jdr/logic.py`
- [X] T025 [US2] Keep session state transitions limited to existing values `audio_uploaded`, `transcribing`, `transcribed`, and `transcription_failed` in `app/jobs/jdr.py`
- [X] T026 [US2] Update the 413 Problem Details detail text and optional extension data so the frontend can display the effective limit in `app/services/jdr/batch/router.py`
- [X] T027 [US2] Update `specs/009-server-audio-reduce/contracts/rest-api.md` if implementation chooses detail-only 413 limit exposure instead of a `limit_bytes` extension

**Checkpoint**: User Stories 1 and 2 work without requiring frontend contract changes.

---

## Phase 5: User Story 3 - Manage Raw and Reduced Audio Deliberately (Priority: P2)

**Goal**: Raw files are deleted after successful preparation, prepared audio is retained for existing session behavior, and destructive delete removes all audio artifacts.

**Independent Test**: Process successful and failed preparations, retrieve audio after success, and delete session audio to verify raw leftovers, prepared audio, transcription, chunks, artifacts, and job pointer are cleaned correctly.

### Tests for User Story 3

- [X] T028 [P] [US3] Add failing audio retrieval test that `GET /audio` serves the prepared retained audio after successful preparation in `tests/services/jdr/test_audio_get.py`
- [X] T029 [P] [US3] Add failing purge test that `DELETE /audio` removes prepared audio and raw leftovers together in `tests/services/jdr/test_audio_purge.py`
- [X] T030 [P] [US3] Add failing worker test that transcription failure after successful preparation keeps prepared audio available for retry/delete in `tests/jobs/test_transcribe_audio_reduce.py`
- [X] T031 [P] [US3] Add failing purge test that deletion removes transcription rows, chunks, artifacts, and `current_job_id` after a prepared-audio session in `tests/services/jdr/test_audio_purge.py`

### Implementation for User Story 3

- [X] T032 [US3] Ensure `get_audio_for_session()` resolves the canonical prepared audio path after preparation in `app/services/jdr/logic.py`
- [X] T033 [US3] Extend `purge_audio_for_session()` to delete both canonical prepared audio and transient raw leftovers for the session in `app/services/jdr/logic.py`
- [X] T034 [US3] Preserve prepared audio and `current_job_id` when transcription fails after successful preparation in `app/jobs/jdr.py`
- [X] T035 [US3] Ensure `purge_audio_for_session()` still clears transcriptions, chunks, artifacts, and `current_job_id` for prepared-audio sessions in `app/services/jdr/logic.py`

**Checkpoint**: All user stories are independently functional and the file lifecycle matches BD-9.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, operational checks, and final validation across the feature.

- [X] T036 [P] Update `README.md` JDR audio documentation with server-side preparation, upload limit, and raw deletion behavior
- [X] T037 [P] Add BD-9 learning entry to `docs/journal.md`
- [X] T038 [P] Update `docs/memo.md` with the final env var name, default value, and verification commands
- [X] T039 [P] Update `specs/009-server-audio-reduce/quickstart.md` if final command names or file layout differ from the plan
- [X] T040 Run `ruff check .` using `pyproject.toml` from repository root and fix any reported issues
- [X] T041 Run `pytest tests/services/jdr/test_audio_upload.py tests/jobs/test_transcribe_audio_reduce.py tests/services/jdr/test_audio_get.py tests/services/jdr/test_audio_purge.py` and fix regressions
- [X] T042 Run full `pytest` over `tests/` from repository root and fix regressions

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: No dependencies.
- **Phase 2 Foundational**: Depends on Phase 1 and blocks all user stories.
- **Phase 3 US1**: Depends on Phase 2 and is the MVP.
- **Phase 4 US2**: Depends on Phase 2; can be implemented in parallel with US1 only after shared foundations exist, but should be validated after US1 because it asserts the upload/job behavior.
- **Phase 5 US3**: Depends on Phase 2 and benefits from US1 prepared-audio behavior.
- **Phase 6 Polish**: Depends on all desired user stories.

### User Story Dependencies

- **US1 Upload Long Session Audio Without Client Reduction**: MVP, no dependency on other user stories after foundations.
- **US2 Keep the Existing Frontend Contract Stable**: Depends on foundations and validates the public contract around US1.
- **US3 Manage Raw and Reduced Audio Deliberately**: Depends on the prepared audio lifecycle introduced by US1.

### Within Each User Story

- Tests first; verify they fail before implementation.
- Shared helpers before worker integration.
- Worker integration before endpoint-level assertions that depend on prepared audio.
- Story checkpoint before moving to the next priority.

## Parallel Opportunities

- T002 and T003 can run in parallel after T001.
- T004 and T006 can be written in parallel because they target different test files.
- T009, T010, T011, and T012 can be written in parallel after Phase 2.
- T019, T020, T021, and T022 can be written in parallel after Phase 2.
- T028, T029, T030, and T031 can be written in parallel after Phase 2.
- T036, T037, T038, and T039 can run in parallel during polish.

## Parallel Example: User Story 1

```text
Task: "T009 [P] [US1] Add failing upload test for accepting raw audio under KAEYRIS_AUDIO_MAX_UPLOAD_BYTES in tests/services/jdr/test_audio_upload.py"
Task: "T010 [P] [US1] Add failing upload test for rejecting oversized raw audio with HTTP 413 in tests/services/jdr/test_audio_upload.py"
Task: "T011 [P] [US1] Add failing worker test for successful preparation before transcription in tests/jobs/test_transcribe_audio_reduce.py"
Task: "T012 [P] [US1] Add failing worker test for preparation failure in tests/jobs/test_transcribe_audio_reduce.py"
```

## Parallel Example: User Story 2

```text
Task: "T019 [P] [US2] Add failing API contract regression test for AudioUploadOut in tests/services/jdr/test_audio_upload.py"
Task: "T020 [P] [US2] Add failing job kind/current_job_id regression test in tests/services/jdr/test_audio_upload.py"
Task: "T021 [P] [US2] Add failing no reducing/audio_reduce exposure test in tests/jobs/test_transcribe_audio_reduce.py"
Task: "T022 [P] [US2] Add failing 413 limit visibility test in tests/services/jdr/test_audio_upload.py"
```

## Parallel Example: User Story 3

```text
Task: "T028 [P] [US3] Add failing prepared audio retrieval test in tests/services/jdr/test_audio_get.py"
Task: "T029 [P] [US3] Add failing purge raw plus prepared audio test in tests/services/jdr/test_audio_purge.py"
Task: "T030 [P] [US3] Add failing transcription failure keeps prepared audio test in tests/jobs/test_transcribe_audio_reduce.py"
Task: "T031 [P] [US3] Add failing purge derived artifacts test in tests/services/jdr/test_audio_purge.py"
```

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 setup.
2. Complete Phase 2 foundations.
3. Complete Phase 3 US1.
4. Stop and validate US1 independently with focused upload and worker tests.

### Incremental Delivery

1. Setup plus foundations: config, audio preparation helper, canonical metadata update.
2. US1: raw upload limit plus worker preparation before transcription.
3. US2: prove the public contract stays stable.
4. US3: complete raw/prepared cleanup and retrieval semantics.
5. Polish: docs, quickstart, ruff, focused tests, full pytest.

### Bias Watch

- Avoid scope creep into a generic media service.
- Avoid adding a new job kind or state unless implementation uncovers a real operational constraint and the product decision is revisited.
- Avoid hiding upload size behind infrastructure only; backend behavior must remain testable.
