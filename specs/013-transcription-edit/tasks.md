# Tasks: BD-13 Transcription Edit

**Input**: Design documents from `specs/013-transcription-edit/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/rest-api.md`, `quickstart.md`
**Tests**: Required by project constitution and BD-13 acceptance criteria. Write tests first and verify they fail before implementation.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it touches different files or does not depend on incomplete code from the same phase.
- **[Story]**: Maps to user stories in `spec.md` (`US1`, `US2`, `US3`).
- Every task includes an exact repository path.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Prepare the persistence surface shared by all stories.

- [X] T001 Create Alembic migration `migrations/versions/0010_session_transcription_edit.py` adding nullable `edited_transcript_md` text column to `jdr_sessions` with downgrade support.
- [X] T002 Add `edited_transcript_md` nullable mapped column to `Session` in `app/services/jdr/db/models.py`.
- [X] T003 [P] Add `TranscriptionEditIn` and `TranscriptionEditOut` schemas with non-blank `content_md` validation in `app/services/jdr/schemas.py`.

**Checkpoint**: Database/model/schema surface exists for user story implementation.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add shared helpers and error translation needed by all stories.

**CRITICAL**: No user story work can be completed until this phase is complete.

- [X] T004 Add a focused session repository update helper for `edited_transcript_md` in `app/services/jdr/db/repositories.py`.
- [X] T005 Add a JDR domain error for editing a non-transcribed session, reusing the existing public `session-not-transcribed` problem type in `app/services/jdr/router.py`.
- [X] T006 Add shared logic helper to load a GM/campaign-scoped session for transcription edit operations in `app/services/jdr/logic.py`.

**Checkpoint**: Foundation ready; user stories can now be implemented in priority order.

---

## Phase 3: User Story 1 - Save And Reuse An Edited Transcription (Priority: P1) MVP

**Goal**: A GM can save corrected Markdown for an owned transcribed session and read the exact edited Markdown through the Markdown transcription export.

**Independent Test**: Create a transcribed session, save edited Markdown, then call `GET /transcription.md` and verify the exact edited content is returned; also verify a session with no edit still returns automatic Markdown.

### Tests for User Story 1

- [X] T007 [P] [US1] Add failing endpoint test for `PUT /services/jdr/sessions/{session_id}/transcription` returning saved `content_md` in `tests/services/jdr/test_transcription_edit.py`.
- [X] T008 [P] [US1] Add failing Markdown read test proving `GET /services/jdr/sessions/{session_id}/transcription.md` returns edited Markdown when present in `tests/services/jdr/test_transcription_edit.py`.
- [X] T009 [P] [US1] Add failing fallback read test proving `GET /services/jdr/sessions/{session_id}/transcription.md` keeps automatic Markdown when no edit exists in `tests/services/jdr/test_transcription_edit.py`.
- [X] T010 [P] [US1] Add failing replacement test proving a second `PUT /transcription` replaces the previous edited Markdown in `tests/services/jdr/test_transcription_edit.py`.
- [X] T011 [P] [US1] Add failing OpenAPI contract test for `PUT /services/jdr/sessions/{session_id}/transcription` and required `content_md` schema in `tests/services/jdr/test_transcription_edit.py`.

### Implementation for User Story 1

- [X] T012 [US1] Implement `save_session_transcription_edit` business operation in `app/services/jdr/logic.py`.
- [X] T013 [US1] Add `PUT /services/jdr/sessions/{session_id}/transcription` endpoint using `TranscriptionEditIn` and `TranscriptionEditOut` in `app/services/jdr/router.py`.
- [X] T014 [US1] Update `GET /services/jdr/sessions/{session_id}/transcription.md` to return `session.edited_transcript_md` when present in `app/services/jdr/router.py`.
- [X] T015 [US1] Ensure non-diarised edited Markdown can be returned by the Markdown export without mutating chunks in `app/services/jdr/router.py`.
- [X] T016 [US1] Run `uv run pytest tests/services/jdr/test_transcription_edit.py -q` and fix US1 regressions.

**Checkpoint**: User Story 1 is fully functional and testable independently.

---

## Phase 4: User Story 2 - Generate Artifacts From Corrected Text (Priority: P2)

**Goal**: Generation jobs launched after an edit use the latest edited Markdown as source text.

**Independent Test**: Save an edited transcription containing distinctive text, run summary/source-selection tests, and verify the LLM receives the edited text rather than the automatic chunks or segments.

### Tests for User Story 2

- [X] T017 [P] [US2] Add failing summary job test proving `_generate_summary` maps over edited Markdown when `edited_transcript_md` exists in `tests/jobs/test_jdr_summary.py`.
- [X] T018 [P] [US2] Add failing summary job fallback test proving `_generate_summary` still uses automatic chunks when no edited Markdown exists in `tests/jobs/test_jdr_summary.py`.
- [X] T019 [P] [US2] Add failing generation source helper test proving narrative/elements/POV source selection prefers `edited_transcript_md` in `tests/jobs/test_jdr_summary.py`.
- [X] T020 [P] [US2] Add failing replacement source test proving the latest saved edited text is used by later generation calls in `tests/jobs/test_jdr_summary.py`.

### Implementation for User Story 2

- [X] T021 [US2] Add helper to select edited Markdown before automatic mode-specific source in `app/jobs/jdr.py`.
- [X] T022 [US2] Update `_load_session_source_document` to use edited Markdown for narrative, elements, and POV generation in `app/jobs/jdr.py`.
- [X] T023 [US2] Update `_generate_summary` to split edited Markdown into transient chunks with `chunk_text` when present in `app/jobs/jdr.py`.
- [X] T024 [US2] Preserve existing `jdr_chunks.text`, `jdr_chunks.summary_text`, and summary artifact behavior for sessions without edited Markdown in `app/jobs/jdr.py`.
- [X] T025 [US2] Run `uv run pytest tests/jobs/test_jdr_summary.py -q` and fix US2 regressions.

**Checkpoint**: User Stories 1 and 2 both work independently.

---

## Phase 5: User Story 3 - Protect Session State And Ownership (Priority: P3)

**Goal**: Editing is rejected for non-transcribed sessions, cross-owner sessions, unauthenticated callers, and blank content without changing existing transcription data.

**Independent Test**: Attempt saves against a non-transcribed session, another GM's session, and blank payloads, then verify the API errors and persisted automatic data remain unchanged.

### Tests for User Story 3

- [X] T026 [P] [US3] Add failing test for `PUT /transcription` on non-transcribed session returning `409 session-not-transcribed` in `tests/services/jdr/test_transcription_edit.py`.
- [X] T027 [P] [US3] Add failing cross-owner test for `PUT /transcription` returning `404 session-not-found` and leaving content unchanged in `tests/services/jdr/test_transcription_edit.py`.
- [X] T028 [P] [US3] Add failing validation tests for missing, null, empty, and whitespace-only `content_md` returning `422` in `tests/services/jdr/test_transcription_edit.py`.
- [X] T029 [P] [US3] Add failing auth tests proving missing credentials and player credentials cannot save edited transcription in `tests/services/jdr/test_transcription_edit.py`.
- [X] T030 [P] [US3] Add failing persistence invariant test proving save does not modify `jdr_transcriptions.segments_json` or `jdr_chunks.text` in `tests/services/jdr/test_transcription_edit.py`.

### Implementation for User Story 3

- [X] T031 [US3] Enforce `state=transcribed` before persisting edited Markdown in `app/services/jdr/logic.py`.
- [X] T032 [US3] Map non-transcribed edit attempts to `409 session-not-transcribed` in `app/services/jdr/router.py`.
- [X] T033 [US3] Ensure `TranscriptionEditIn.content_md` rejects missing, null, empty, and whitespace-only content in `app/services/jdr/schemas.py`.
- [X] T034 [US3] Ensure edit endpoint uses existing `require_gm` ownership behavior and hides cross-owner sessions as `404 session-not-found` in `app/services/jdr/router.py`.
- [X] T035 [US3] Run `uv run pytest tests/services/jdr/test_transcription_edit.py -q` and fix US3 regressions.

**Checkpoint**: All user stories are independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Contract, docs, and final validation for BD-13.

- [X] T036 [P] Regenerate the frontend OpenAPI contract in `docs/context/api/openapi.json`.
- [X] T037 [P] Document editable transcription behavior in `docs/services/jdr.md`.
- [X] T038 [P] Add BD-13 command/reference rows to `docs/memo.md`.
- [X] T039 [P] Add BD-13 learning entry to `docs/journal.md`.
- [X] T040 [P] Update `specs/013-transcription-edit/quickstart.md` if final endpoint names or response fields differ from the plan.
- [X] T041 Run `uv run ruff check .` from repository root and fix reported issues.
- [X] T042 Run `uv run pytest tests/services/jdr/test_transcription_edit.py tests/jobs/test_jdr_summary.py -q` from repository root and fix regressions.
- [X] T043 Run full `uv run pytest -q` from repository root and fix regressions.
- [X] T044 Run `docker compose config --quiet` from repository root and fix Compose validation issues.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; start immediately.
- **Foundational (Phase 2)**: Depends on Phase 1; blocks all user stories.
- **US1 Save/read edit (Phase 3)**: Depends on Phase 2; MVP.
- **US2 Generation source (Phase 4)**: Depends on Phase 2 and benefits from US1 data model/API, but can be tested at job level with seeded edited content.
- **US3 Protection rules (Phase 5)**: Depends on Phase 2 and should be completed before PR.
- **Polish (Phase 6)**: Depends on completed desired stories.

### User Story Dependencies

- **US1 (P1)**: MVP, no dependency on US2 or US3 after foundation.
- **US2 (P2)**: Uses the same persisted field as US1; job-level work can begin after foundation once model exists.
- **US3 (P3)**: Hardening around US1 endpoint; can be developed in parallel with US2 after foundation.

### Within Each User Story

- Tests first; verify they fail before implementation.
- Model/schema before logic.
- Logic before router endpoint.
- Source-selection helper before generation job integration.
- Story checkpoint before moving to the next priority.

### Parallel Opportunities

- T003 can run alongside T001 and T002 after agreeing on schema names.
- T007 through T011 can be written in parallel because they are tests in one file with separate scenarios.
- T017 through T020 can be written in parallel because they cover distinct generation behaviors.
- T026 through T030 can be written in parallel because they cover independent protection scenarios.
- T036 through T040 can run in parallel after implementation stabilizes.

---

## Parallel Example: User Story 1

```text
Task: "T007 [P] [US1] Add failing endpoint test for PUT /transcription in tests/services/jdr/test_transcription_edit.py"
Task: "T008 [P] [US1] Add failing Markdown read test for edited override in tests/services/jdr/test_transcription_edit.py"
Task: "T009 [P] [US1] Add failing fallback read test in tests/services/jdr/test_transcription_edit.py"
Task: "T011 [P] [US1] Add failing OpenAPI contract test in tests/services/jdr/test_transcription_edit.py"
```

## Parallel Example: User Story 2

```text
Task: "T017 [P] [US2] Add failing summary job test for edited Markdown source in tests/jobs/test_jdr_summary.py"
Task: "T018 [P] [US2] Add failing summary fallback test in tests/jobs/test_jdr_summary.py"
Task: "T019 [P] [US2] Add failing source helper test for narrative/elements/POV in tests/jobs/test_jdr_summary.py"
```

## Parallel Example: User Story 3

```text
Task: "T026 [P] [US3] Add failing non-transcribed edit test in tests/services/jdr/test_transcription_edit.py"
Task: "T027 [P] [US3] Add failing cross-owner edit test in tests/services/jdr/test_transcription_edit.py"
Task: "T028 [P] [US3] Add failing blank content validation tests in tests/services/jdr/test_transcription_edit.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 and Phase 2.
2. Complete Phase 3.
3. Run `uv run pytest tests/services/jdr/test_transcription_edit.py -q`.
4. Confirm a GM can save and read edited Markdown while fallback reads still work.

### Incremental Delivery

1. Foundation: migration/model/schema/logic surface.
2. US1: API save/read behavior and OpenAPI exposure.
3. US2: generation consumes edited source.
4. US3: ownership/state/validation hardening.
5. Polish: docs, OpenAPI regeneration, full checks.

### Final Validation

1. `uv run ruff check .`
2. `uv run pytest tests/services/jdr/test_transcription_edit.py tests/jobs/test_jdr_summary.py -q`
3. `uv run pytest -q`
4. `docker compose config --quiet`

## Notes

- Do not add reset/delete in BD-13.
- Do not mutate automatic diarised segments or non-diarised chunk text.
- Do not add vendor-specific logic in `app/services/jdr`.
- Keep API errors consistent with existing JDR Problem Details categories.
