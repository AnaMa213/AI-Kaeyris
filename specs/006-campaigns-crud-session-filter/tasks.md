# Tasks: Campaigns CRUD and Session Campaign Filter

**Input**: Design documents from `D:\Projets\dev\AI-Kaeyris\specs\006-campaigns-crud-session-filter\`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/rest-api.md`, `quickstart.md`

**Tests**: Required by project constitution. Write endpoint, authorization, migration/backfill, OpenAPI, and regression tests before implementation.

**Organization**: Tasks are grouped by user story to enable independent implementation and validation.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete tasks)
- **[Story]**: User story label (`US1`, `US2`, `US3`, `US4`)
- Every task includes exact file paths

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm BD-6 starts from the merged BD-4/BD-5 code and prepare shared test surfaces.

- [X] T001 Review existing campaign ORM fields, session campaign behavior, and PJ campaign behavior in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\models.py`
- [X] T002 [P] Review existing campaign repository and session repository methods in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\repositories.py`
- [X] T003 [P] Review existing web-session auth and campaign scope helpers in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\campaign_context.py`
- [X] T004 [P] Add or adapt BD-6 fixture helpers for multi-campaign users, memberships, and sessions in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\campaign_fixtures.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared schema, migration, repository, and authorization primitives required by every user story.

**CRITICAL**: No user story work should start until this phase is complete.

### Tests for Foundational Work

- [X] T005 [P] Add schema/migration smoke tests for campaign `description` and legacy session campaign backfill in `D:\Projets\dev\AI-Kaeyris\tests\core\test_campaign_context.py`
- [X] T006 [P] Add repository unit tests for campaign membership lookup, GM-role checks, duplicate-name detection, session counts, and last-session aggregate in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_memberships.py`
- [X] T007 [P] Add timezone serialization regression coverage for future `CampaignOut.created_at` and `CampaignOut.last_session_at` fields in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_datetime_serialization.py`

### Implementation for Foundational Work

- [X] T008 Add `Campaign.description` ORM/storage support if absent and create Alembic migration `0007_campaigns_crud.py` in `D:\Projets\dev\AI-Kaeyris\migrations\versions\0007_campaigns_crud.py`
- [X] T009 Update `adopt_existing_users_into_default_campaign` to backfill only legacy sessions while keeping BD-6 PJ public behavior global in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\campaign_context.py`
- [X] T010 Add campaign authorization helpers for member-required and GM-required access in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\campaign_context.py`
- [X] T011 Expand `CampaignRepository` with list, detail, create, update, delete, duplicate-name, session-count, and last-session aggregate helpers in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\repositories.py`
- [X] T012 Add `CampaignCreate`, `CampaignPatch`, `CampaignOut`, and campaign page response schemas using BD-5 datetime serialization in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\schemas.py`
- [X] T013 Add reusable campaign HTTP errors for not-found, forbidden, duplicate-name, and delete-conflict cases in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py`
- [X] T014 Run foundational tests from `D:\Projets\dev\AI-Kaeyris`: `pytest tests\core\test_campaign_context.py tests\services\jdr\test_campaign_memberships.py tests\services\jdr\test_datetime_serialization.py -q`

**Checkpoint**: Campaign storage, schemas, membership guards, and aggregate helpers are ready for endpoint work.

---

## Phase 3: User Story 1 - View My Campaigns (Priority: P1) MVP

**Goal**: A signed-in user can list and fetch only campaigns they belong to, with role, session count, last session date, and creation date.

**Independent Test**: Seed a user with multiple campaign memberships and sessions, call campaign list/detail with the session cookie, and verify only member campaigns are visible with correct summary fields.

### Tests for User Story 1

- [X] T015 [US1] Add `GET /services/jdr/campaigns` success, empty-list, role, aggregate, and timezone contract tests in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaigns_crud.py`
- [X] T016 [US1] Add `GET /services/jdr/campaigns/{campaign_id}` success, non-member 403, and missing-campaign 404 tests in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaigns_crud.py`
- [X] T017 [US1] Add OpenAPI tests for campaign list/detail response schemas in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaigns_crud.py`

### Implementation for User Story 1

- [X] T018 [US1] Implement campaign list and detail service functions in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\logic.py`
- [X] T019 [US1] Implement `GET /services/jdr/campaigns` and `GET /services/jdr/campaigns/{campaign_id}` routes in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py`
- [X] T020 [US1] Ensure campaign list/detail routes require cookie/web-session membership without leaking non-member campaigns in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py`
- [X] T021 [US1] Run focused campaign read tests from `D:\Projets\dev\AI-Kaeyris`: `pytest tests\services\jdr\test_campaigns_crud.py -q`

**Checkpoint**: Frontend can replace campaign-list mocks and display campaign cards from live data.

---

## Phase 4: User Story 2 - Create and Manage a Campaign (Priority: P1)

**Goal**: A signed-in game master can create a campaign, become GM of it, and update name or description.

**Independent Test**: Create a campaign, fetch it, patch its name/description, and verify player users cannot patch it.

### Tests for User Story 2

- [X] T022 [US2] Add `POST /services/jdr/campaigns` tests for success, creator GM membership, validation errors, duplicate-name 409, and timezone output in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaigns_crud.py`
- [X] T023 [US2] Add `PATCH /services/jdr/campaigns/{campaign_id}` tests for partial name/description update, player 403, non-member 403, missing 404, duplicate-name 409, and validation errors in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaigns_crud.py`
- [X] T024 [US2] Add OpenAPI tests for campaign create/patch request schemas in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaigns_crud.py`

### Implementation for User Story 2

- [X] T025 [US2] Implement campaign create and update service functions in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\logic.py`
- [X] T026 [US2] Implement `POST /services/jdr/campaigns` and `PATCH /services/jdr/campaigns/{campaign_id}` routes in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py`
- [X] T027 [US2] Ensure campaign creation writes creator membership with `gm` role atomically in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\logic.py`
- [X] T028 [US2] Ensure campaign update requires current user GM membership and maps duplicate names to 409 in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py`
- [X] T029 [US2] Run focused campaign write tests from `D:\Projets\dev\AI-Kaeyris`: `pytest tests\services\jdr\test_campaigns_crud.py -q`

**Checkpoint**: Frontend can create and edit campaigns through live endpoints.

---

## Phase 5: User Story 3 - Use Campaign Context for Sessions (Priority: P1)

**Goal**: New sessions require an explicit campaign and session listing can be filtered by campaign without exposing cross-campaign data.

**Independent Test**: Create two campaigns and sessions under the same user, then verify filtered list/create/detail authorization behavior.

### Tests for User Story 3

- [X] T030 [US3] Add `POST /services/jdr/sessions` tests requiring `campaign_id`, accepting valid GM campaign, rejecting player/non-member campaign, and preserving BD-5 datetime input behavior in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_sessions.py`
- [X] T031 [US3] Add `GET /services/jdr/sessions?campaign_id=...` tests for filtered results, non-member 403, invalid UUID 422, and unfiltered backward-compatible behavior in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_sessions.py`
- [X] T032 [US3] Add `GET /services/jdr/sessions/{session_id}` tests proving campaign membership is checked against the session campaign in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_sessions.py`
- [X] T033 [US3] Add regression tests proving `GET /services/jdr/pjs` and `POST /services/jdr/pjs` remain user-global for BD-6 public behavior in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_sessions.py`
- [X] T034 [US3] Add OpenAPI tests for session list `campaign_id` query parameter and required session create `campaign_id` body field in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_sessions.py`

### Implementation for User Story 3

- [X] T035 [US3] Add required `campaign_id` to `SessionCreate` while preserving `recorded_at` timezone normalization in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\schemas.py`
- [X] T036 [US3] Update session create service logic to require GM membership for the requested campaign in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\logic.py`
- [X] T037 [US3] Update `POST /services/jdr/sessions` to use payload `campaign_id` instead of active/default campaign fallback in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py`
- [X] T038 [US3] Update `GET /services/jdr/sessions` route signature to accept optional `campaign_id` query parameter and enforce membership when present in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py`
- [X] T039 [US3] Update session detail lookup to distinguish missing sessions from forbidden cross-campaign membership according to the BD-6 contract in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py`
- [X] T040 [US3] Adjust PJ create/list route behavior to remain user-global for BD-6 public endpoints in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py`
- [X] T041 [US3] Adjust `PjRepository.create`, `PjRepository.list_for_gm`, and `PjRepository.find_by_id_owned_by` only as needed to support user-global BD-6 behavior in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\repositories.py`
- [X] T042 [US3] Run focused campaign session tests from `D:\Projets\dev\AI-Kaeyris`: `pytest tests\services\jdr\test_campaign_sessions.py tests\services\jdr\test_campaign_isolation.py -q`

**Checkpoint**: Campaign selection drives session create/list flows while legacy unfiltered session listing remains available.

---

## Phase 6: User Story 4 - Safely Delete Empty Campaigns (Priority: P2)

**Goal**: A campaign GM can delete an empty campaign, while campaigns with sessions and player attempts are rejected.

**Independent Test**: Delete an empty campaign and verify it disappears, then attempt deletion of a campaign with sessions and as a player.

### Tests for User Story 4

- [X] T043 [US4] Add `DELETE /services/jdr/campaigns/{campaign_id}` tests for empty campaign 204, with-sessions 409, player 403, non-member 403, and missing 404 in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaigns_crud.py`
- [X] T044 [US4] Add regression test proving delete does not remove sessions or memberships when returning 409 in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaigns_crud.py`

### Implementation for User Story 4

- [X] T045 [US4] Implement empty-campaign delete service function with session-count guard in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\logic.py`
- [X] T046 [US4] Implement `DELETE /services/jdr/campaigns/{campaign_id}` route returning 204 or mapped Problem Details errors in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py`
- [X] T047 [US4] Run focused campaign delete tests from `D:\Projets\dev\AI-Kaeyris`: `pytest tests\services\jdr\test_campaigns_crud.py -q`

**Checkpoint**: Users can clean up empty campaigns without risking session history loss.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, contract verification, and full quality gate.

- [X] T048 [P] Update JDR service documentation with campaign CRUD and session filter behavior in `D:\Projets\dev\AI-Kaeyris\docs\services\jdr.md`
- [X] T049 [P] Update command reference for campaign CRUD and filtered sessions in `D:\Projets\dev\AI-Kaeyris\docs\memo.md`
- [X] T050 [P] Add journal entry for BD-6 campaign CRUD and explicit session campaign selection in `D:\Projets\dev\AI-Kaeyris\docs\journal.md`
- [X] T051 [P] Update README endpoint table and JDR usage examples for campaign CRUD and `campaign_id` session creation in `D:\Projets\dev\AI-Kaeyris\README.md`
- [X] T052 Add ADR only if implementation requires a significant schema/authorization decision beyond the BD-6 plan in `D:\Projets\dev\AI-Kaeyris\docs\adr\0013-campaigns-crud-session-filter.md` (no ADR required)
- [X] T053 Run quickstart validation commands from `D:\Projets\dev\AI-Kaeyris\specs\006-campaigns-crud-session-filter\quickstart.md`
- [X] T054 Run full linter from `D:\Projets\dev\AI-Kaeyris`: `ruff check .`
- [X] T055 Run full test suite from `D:\Projets\dev\AI-Kaeyris`: `pytest`
- [X] T056 Verify Docker Compose config still renders from `D:\Projets\dev\AI-Kaeyris\docker-compose.yml`: `docker compose config`
- [X] T057 Manually verify `/openapi.json` exposes the five campaign endpoints and session `campaign_id` changes from `D:\Projets\dev\AI-Kaeyris`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: no dependencies.
- **Phase 2 Foundational**: depends on Phase 1; blocks every user story.
- **US1 (P1)**: depends on Phase 2 and delivers the MVP campaign read surface.
- **US2 (P1)**: depends on Phase 2; can be developed after or alongside US1 but should reuse the same schemas and repository helpers.
- **US3 (P1)**: depends on Phase 2; safest after US1/US2 because it relies on live campaign selection.
- **US4 (P2)**: depends on US1/US2 because campaign detail and session counts must exist.
- **Polish**: depends on all desired user stories being complete.

### User Story Dependencies

- **US1 (P1)**: Independent read-only MVP once foundational repository helpers exist.
- **US2 (P1)**: Independent write workflow once foundational schemas and authorization helpers exist.
- **US3 (P1)**: Integrates sessions with campaign IDs and should be validated against US1/US2 data.
- **US4 (P2)**: Requires session-count guard and campaign authorization from earlier stories.

### Within Each User Story

- Write tests first and verify they fail.
- Update schemas before routes that expose them.
- Update repository helpers before logic functions.
- Update logic functions before route handlers.
- Run focused tests at each checkpoint before moving to the next phase.

---

## Parallel Opportunities

- T002, T003, and T004 can run in parallel after T001 starts.
- T005, T006, and T007 can be written in parallel because they target different test files.
- T015 through T017 are sequential in one file; split `test_campaigns_crud.py` if parallel authoring becomes necessary.
- T022 through T024 are sequential in one file; they can be drafted after T015-T017 are stable.
- T030 through T034 share `test_campaign_sessions.py`, so keep them sequential unless the file is split.
- T048 through T051 can run in parallel after behavior is stable.

## Parallel Example: Foundational Tests

```text
Task: "T005 Add schema/migration smoke tests for campaign description and legacy session campaign backfill in tests/core/test_campaign_context.py"
Task: "T006 Add repository unit tests for campaign membership lookup, GM-role checks, duplicate-name detection, session counts, and last-session aggregate in tests/services/jdr/test_campaign_memberships.py"
Task: "T007 Add timezone serialization regression coverage for future CampaignOut fields in tests/services/jdr/test_datetime_serialization.py"
```

## Parallel Example: Polish

```text
Task: "T048 Update JDR service documentation with campaign CRUD and session filter behavior in docs/services/jdr.md"
Task: "T049 Update command reference for campaign CRUD and filtered sessions in docs/memo.md"
Task: "T050 Add journal entry for BD-6 campaign CRUD and explicit session campaign selection in docs/journal.md"
Task: "T051 Update README endpoint table and JDR usage examples in README.md"
```

---

## Implementation Strategy

### MVP First

1. Complete Phase 1 and Phase 2.
2. Complete US1 campaign list/detail.
3. Validate frontend can read live campaigns before implementing mutation flows.

### Incremental Delivery

1. US1: campaign list/detail.
2. US2: campaign create/update.
3. US3: session campaign creation/filtering.
4. US4: safe delete.
5. Polish and full quality gates.

### Safety Notes

- Do not add campaign-scoped PJs in BD-6; that is explicitly separate future scope.
- Do not cascade-delete campaigns with sessions.
- Do not remove unfiltered session listing until frontend and backend agree on a breaking API change.
- Keep BD-5 datetime serialization active for every new campaign datetime field.
