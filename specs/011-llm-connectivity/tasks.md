# Tasks: BD-11 LLM Connectivity

**Input**: Design documents from `specs/011-llm-connectivity/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/rest-api.md`, `quickstart.md`

**Tests**: Included because the feature specification and project constitution require regression coverage for public job behavior and transcription safety.

**Organization**: Tasks are grouped by user story so each increment can be implemented and tested independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel with other marked tasks in the same phase because it touches different files or only reads context
- **[Story]**: User story label for traceability
- Every task includes an exact repository path

---

## Phase 1: Setup (Shared Context)

**Purpose**: Establish the exact current behavior and the likely worker connectivity gap before changing code.

- [X] T001 Inspect current LLM env propagation for `api` and `worker` services in `docker-compose.yml`
- [X] T002 Inspect current LLM settings and adapter factory defaults in `app/core/config.py` and `app/adapters/llm.py`
- [X] T003 [P] Inspect existing summary job tests and fixtures in `tests/jobs/test_jdr_summary.py`
- [X] T004 [P] Inspect existing summary route tests in `tests/services/jdr/test_summary.py`
- [X] T005 [P] Inspect existing job polling tests in `tests/services/jdr/test_jobs_route.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add shared coverage for configuration/failure primitives that all user stories depend on.

**CRITICAL**: No user story implementation should begin until these tests define the expected shared behavior.

- [X] T006 [P] Add adapter factory test proving explicit `LLM_BASE_URL` is passed to `OpenAICompatibleLLMAdapter` in `tests/adapters/test_llm.py`
- [X] T007 [P] Add adapter error mapping assertion for `APIConnectionError` message readability in `tests/adapters/test_llm.py`
- [X] T008 Add Docker worker reachability decision test or static check for local host gateway support in `tests/test_docker_compose.py`
- [X] T009 Update `.env.example` comments to state that `LLM_BASE_URL` must be reachable from the `worker` container

**Checkpoint**: Shared config behavior is specified; user story implementation can begin.

---

## Phase 3: User Story 1 - Generate JDR Artifacts End To End (Priority: P1) MVP

**Goal**: A valid transcribed non-diarised session can enqueue a summary job, run LLM map/reduce through the worker path, and expose the resulting summary artifact.

**Independent Test**: Start from a transcribed non-diarised session with chunks, run summary generation with a reachable mock/stub adapter, then verify job success and summary artifact availability.

### Tests for User Story 1

- [X] T010 [P] [US1] Add route contract test for `POST /services/jdr/sessions/{session_id}/artifacts/summary` returning a `summary` queued job in `tests/services/jdr/test_summary.py`
- [X] T011 [P] [US1] Add job test proving `_generate_summary` succeeds with a reachable adapter and writes `Artifact(kind="summary")` in `tests/jobs/test_jdr_summary.py`
- [X] T012 [P] [US1] Add integration-style test covering summary enqueue then `GET /services/jdr/jobs/{job_id}` projection for summary jobs in `tests/services/jdr/test_jobs_route.py`

### Implementation for User Story 1

- [X] T013 [US1] Ensure `api` and `worker` services share the same LLM env file and Docker host alias configuration in `docker-compose.yml`
- [X] T014 [US1] Add explicit Docker-reachable base URL handling or validation message for local provider URLs in `app/adapters/llm.py`
- [X] T015 [US1] Ensure summary enqueue still uses `transient_errors=True` and returns unchanged `JobQueuedOut` fields in `app/services/jdr/router.py`
- [X] T016 [US1] Ensure successful summary generation leaves `failure_reason` null and writes the existing summary artifact shape in `app/jobs/jdr.py`
- [X] T017 [US1] Run targeted US1 checks with `pytest tests/adapters/test_llm.py tests/jobs/test_jdr_summary.py tests/services/jdr/test_summary.py tests/services/jdr/test_jobs_route.py`

**Checkpoint**: User Story 1 is functional and independently testable.

---

## Phase 4: User Story 2 - Expose Actionable LLM Failure State (Priority: P2)

**Goal**: When the LLM provider remains unreachable after retry exhaustion, the public job endpoint returns `status="failed"` with a non-empty readable `failure_reason`.

**Independent Test**: Force a summary job to fail with an LLM connectivity error, mark it failed through RQ/job projection, then poll `GET /services/jdr/jobs/{job_id}`.

### Tests for User Story 2

- [X] T018 [P] [US2] Add `_generate_summary` test mapping `TransientLLMError("APIConnectionError: Connection error.")` to `TransientJobError` in `tests/jobs/test_jdr_summary.py`
- [X] T019 [P] [US2] Add job route test for a failed summary RQ job exposing non-empty `failure_reason` in `tests/services/jdr/test_jobs_route.py`
- [X] T020 [P] [US2] Add test proving failed summary generation does not create or overwrite `Artifact(kind="summary")` in `tests/jobs/test_jdr_summary.py`

### Implementation for User Story 2

- [X] T021 [US2] Preserve the underlying LLM error class/name in transient summary failures in `app/jobs/jdr.py`
- [X] T022 [US2] Harden `GET /services/jdr/jobs/{job_id}` failed-state projection so `failure_reason` is non-empty and concise for failed jobs in `app/services/jdr/router.py`
- [X] T023 [US2] Ensure failed summary attempts do not produce a summary artifact in `app/jobs/jdr.py`
- [X] T024 [US2] Run targeted US2 checks with `pytest tests/jobs/test_jdr_summary.py tests/services/jdr/test_jobs_route.py`

**Checkpoint**: User Stories 1 and 2 both work independently.

---

## Phase 5: User Story 3 - Avoid Transcription Regressions (Priority: P3)

**Goal**: LLM connectivity changes do not affect transcription behavior, including audio reduce and non-diarised transcription flows.

**Independent Test**: Run the existing transcription tests with LLM provider settings absent or unreachable and confirm they remain green.

### Tests for User Story 3

- [X] T025 [P] [US3] Add regression test proving transcription job code does not instantiate the LLM adapter in `tests/services/jdr/test_transcription_flow.py`
- [X] T026 [P] [US3] Add regression test proving non-diarised transcription flow does not require `LLM_API_KEY` in `tests/services/jdr/test_transcription_flow_non_diarised.py`

### Implementation for User Story 3

- [X] T027 [US3] Keep LLM-specific validation out of transcription adapter and transcription jobs in `app/adapters/transcription.py` and `app/jobs/jdr.py`
- [X] T028 [US3] Run transcription regression checks with `pytest tests/services/jdr/test_transcription_flow.py tests/services/jdr/test_transcription_flow_non_diarised.py tests/jobs/test_transcribe_audio_reduce.py tests/jobs/test_transcribe_chunking.py`

**Checkpoint**: All user stories are independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, manual verification, and full quality gates.

- [X] T029 [P] Update LLM Docker connectivity instructions in `README.md`
- [X] T030 [P] Update quick command/reference notes for worker LLM env verification in `docs/memo.md`
- [X] T031 Add BD-11 learning entry with observed cause/fix in `docs/journal.md`
- [X] T032 Run full lint with `ruff check .`
- [X] T033 Run full test suite with `pytest`
- [X] T034 Run Docker Compose syntax check with `docker compose config --quiet`
- [ ] T035 Execute the BD-11 manual verification steps from `specs/011-llm-connectivity/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; can start immediately.
- **Foundational (Phase 2)**: Depends on Setup; blocks user story implementation.
- **US1 (Phase 3)**: Depends on Foundational; MVP scope.
- **US2 (Phase 4)**: Depends on Foundational; can be implemented after or alongside US1 once shared tests exist, but final validation should include US1.
- **US3 (Phase 5)**: Depends on Foundational; can be validated after LLM changes.
- **Polish (Phase 6)**: Depends on selected user stories being complete.

### User Story Dependencies

- **US1 (P1)**: No dependency on US2 or US3 after Foundational.
- **US2 (P2)**: Uses the same summary job path as US1, but has independent failed-job tests.
- **US3 (P3)**: Independent regression story; must remain green after US1/US2 changes.

### Within Each User Story

- Write/adjust tests first and confirm they fail for the missing behavior.
- Implement the narrowest code/config change needed to satisfy the story.
- Run targeted tests before moving to the next story.
- Avoid changing public frontend contracts unless a task explicitly says so; none do.

---

## Parallel Execution Examples

### User Story 1

```text
Task: "T010 Add route contract test in tests/services/jdr/test_summary.py"
Task: "T011 Add job success test in tests/jobs/test_jdr_summary.py"
Task: "T012 Add job projection test in tests/services/jdr/test_jobs_route.py"
```

### User Story 2

```text
Task: "T018 Add transient mapping test in tests/jobs/test_jdr_summary.py"
Task: "T019 Add failed summary polling test in tests/services/jdr/test_jobs_route.py"
Task: "T020 Add no-artifact-on-failure test in tests/jobs/test_jdr_summary.py"
```

### User Story 3

```text
Task: "T025 Add transcription no-LLM-instantiation test in tests/services/jdr/test_transcription_flow.py"
Task: "T026 Add non-diarised transcription no-LLM-key test in tests/services/jdr/test_transcription_flow_non_diarised.py"
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1 and Phase 2.
2. Complete Phase 3.
3. Validate with targeted summary and adapter tests.
4. Manually verify a reachable provider through the worker path.

### Incremental Delivery

1. US1 restores successful summary generation.
2. US2 makes provider outages visible and retryable from the frontend.
3. US3 confirms transcription was not coupled to LLM connectivity.
4. Polish runs project gates and documentation updates.

### Notes

- `[P]` tasks should only be run in parallel when they do not edit the same file.
- Do not commit real `.env` secrets.
- Keep provider-specific code in `app/adapters/` or configuration only.
- Stop and reassess if implementation discovers a required database migration; the current plan assumes none.
