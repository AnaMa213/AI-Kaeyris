# Tasks: Campaign Auth Context

**Input**: Design documents from `specs/004-campaign-auth-context/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/rest-api.md`, `quickstart.md`

**Tests**: Required by project constitution. Write focused tests before implementation for the new public endpoint, non-trivial campaign resolution, migration/backfill, and campaign scoping.

**Organization**: Tasks are grouped by user story to enable independent implementation and validation.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete tasks)
- **[Story]**: User story label (`US1`, `US2`, `US3`, `US4`)
- Every task includes exact file paths

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add shared campaign vocabulary and test helpers without changing runtime behavior yet.

- [X] T001 Create campaign constants, V1 default campaign id, and `Profile` to membership-role mapping helpers in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\campaigns.py`
- [X] T002 [P] Create campaign test factory helpers for users, campaigns, memberships, sessions, and PJs in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\campaign_factories.py`
- [X] T003 [P] Add BD-4 quick reference rows for `/services/jdr/auth/me` and campaign context commands in `D:\Projets\dev\AI-Kaeyris\docs\memo.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Database schema and active-campaign primitives required by every user story.

**CRITICAL**: No user story work should start until this phase is complete.

- [X] T004 Add unit tests for default campaign id, `Profile` to campaign role mapping, and null-campaign handling in `D:\Projets\dev\AI-Kaeyris\tests\core\test_campaign_context.py`
- [X] T005 [P] Add ORM relationship tests for `Campaign`, `CampaignMember`, same-campaign `character_id`, `User.default_campaign_id`, `Session.campaign_id`, and `Pj.campaign_id` in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_memberships.py`
- [X] T006 Add `CampaignRole`, `Campaign`, `CampaignMember`, `Session.campaign_id`, and `Pj.campaign_id` ORM mappings in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\models.py`
- [X] T007 Add `default_campaign_id` and optional relationship metadata to `User` in `D:\Projets\dev\AI-Kaeyris\app\core\models.py`
- [X] T008 Create Alembic migration `0006_campaign_auth_context.py` for campaign tables, membership table, `core_users.default_campaign_id`, `jdr_sessions.campaign_id`, and `jdr_pjs.campaign_id` in `D:\Projets\dev\AI-Kaeyris\migrations\versions\0006_campaign_auth_context.py`
- [X] T009 Implement `CampaignRepository` methods for create/get default campaign, membership upsert with same-campaign `character_id` validation, membership lookup, and active-campaign lookup in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\repositories.py`
- [X] T010 Implement active campaign dataclasses, deterministic default campaign owner selection, `ensure_default_campaign`, `ensure_user_membership`, and `resolve_active_campaign_for_user` in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\campaigns.py`
- [X] T011 Add a web-session-only auth dependency returning the current `User` for `/auth/me` in `D:\Projets\dev\AI-Kaeyris\app\core\auth.py`
- [X] T012 Run foundational focused tests for campaign primitives in `D:\Projets\dev\AI-Kaeyris\tests\core\test_campaign_context.py` and `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_memberships.py`

**Checkpoint**: Campaign tables, membership primitives, and active campaign resolution exist and can be tested without any new endpoint.

---

## Phase 3: User Story 1 - Current user campaign context (Priority: P1) MVP

**Goal**: A logged-in frontend user can call `GET /services/jdr/auth/me` and receive user identity plus active campaign context.

**Independent Test**: With valid web sessions for one MJ and one player, call `/services/jdr/auth/me` and verify `{ user, active_campaign }`, role mapping, `character_id`, `Cache-Control: no-store`, and 401 for unauthenticated requests.

### Tests for User Story 1

- [X] T013 [US1] Add `/services/jdr/auth/me` success tests for MJ and player web sessions in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_auth_me.py`
- [X] T014 [US1] Add `/services/jdr/auth/me` 401 and `active_campaign: null` tests in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_auth_me.py`
- [X] T015 [P] [US1] Add active-campaign resolver tests for default campaign, stale default fallback, first membership fallback, and no membership in `D:\Projets\dev\AI-Kaeyris\tests\core\test_campaign_context.py`

### Implementation for User Story 1

- [X] T016 [US1] Add `AuthMeUserOut`, `AuthMeCampaignOut`, and `AuthMeOut` Pydantic schemas in `D:\Projets\dev\AI-Kaeyris\app\core\user_schemas.py`
- [X] T017 [US1] Implement `GET /services/jdr/auth/me` with web-session auth, active campaign resolution, and `Cache-Control: no-store` in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T018 [US1] Add structured logs for `/auth/me` success, no-campaign, and unauthorized paths without leaking session tokens in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T019 [US1] Ensure FastAPI OpenAPI exposes the `/services/jdr/auth/me` response model in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T020 [US1] Run focused `/auth/me` tests in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_auth_me.py` and resolver tests in `D:\Projets\dev\AI-Kaeyris\tests\core\test_campaign_context.py`

**Checkpoint**: The frontend can replace its `/auth/me` mock for users who already have campaign memberships.

---

## Phase 4: User Story 2 - Default campaign membership for existing users (Priority: P2)

**Goal**: Existing users and local seed flows automatically receive the V1 default campaign membership.

**Independent Test**: Apply the BD-4 migration or startup seed against a database containing existing users, then verify every active user has one default campaign, one membership, and the role derived from `profile`.

### Tests for User Story 2

- [X] T021 [P] [US2] Add migration/backfill tests for existing `gm` and `user` rows in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_memberships.py`
- [X] T022 [P] [US2] Add startup/default-campaign idempotency and deterministic owner-selection tests proving repeated runs do not duplicate campaign or membership rows in `D:\Projets\dev\AI-Kaeyris\tests\core\test_campaign_context.py`

### Implementation for User Story 2

- [X] T023 [US2] Extend `0006_campaign_auth_context.py` to backfill the V1 default campaign with deterministic owner selection, memberships, `core_users.default_campaign_id`, `jdr_sessions.campaign_id`, and `jdr_pjs.campaign_id` in `D:\Projets\dev\AI-Kaeyris\migrations\versions\0006_campaign_auth_context.py`
- [X] T024 [US2] Call `ensure_default_campaign` during application startup after API-key bootstrap in `D:\Projets\dev\AI-Kaeyris\app\main.py`
- [X] T025 [US2] Add startup logging for default campaign creation/backfill counts in `D:\Projets\dev\AI-Kaeyris\app\main.py`
- [X] T026 [US2] Run membership migration/idempotency tests in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_memberships.py` and `D:\Projets\dev\AI-Kaeyris\tests\core\test_campaign_context.py`

**Checkpoint**: Existing and fresh local users have a deterministic V1 campaign context.

---

## Phase 5: User Story 3 - Campaign-scoped JDR data access (Priority: P3)

**Goal**: JDR reads and writes use the active campaign derived from authentication, never a `campaign_id` supplied by the frontend.

**Independent Test**: Create two campaigns in test data, authenticate as a member of campaign A, create/list/read/update JDR data, and verify campaign B data is invisible or rejected through existing not-found/forbidden behavior.

### Tests for User Story 3

- [X] T027 [US3] Add session creation/list isolation tests with two campaigns in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_scoping.py`
- [X] T028 [US3] Add single-session child route isolation tests for audio, chunks, mapping, players, transcription, artifacts, player-facing `/me/*` routes, and `GET /services/jdr/jobs/{job_id}` in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_scoping.py`
- [X] T029 [US3] Add PJ list/create and PJ validation isolation tests for mappings, session players, player enrolment, and POVs in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_scoping.py`
- [X] T030 [US3] Add negative tests proving explicit `campaign_id` in create/update request bodies is rejected with validation error semantics in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_scoping.py`

### Implementation for User Story 3

- [X] T031 [US3] Update `SessionRepository.create`, `list_for_gm`, and `get_for_gm` to accept and filter by active `campaign_id` in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\repositories.py`
- [X] T032 [US3] Update `PjRepository.create`, `list_for_gm`, and `find_by_id_owned_by` to accept and filter by active `campaign_id` in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\repositories.py`
- [X] T033 [US3] Update JDR session and PJ orchestration functions to accept active campaign context in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\logic.py`
- [X] T034 [US3] Resolve active campaign in session and PJ routes and pass `campaign_id` into logic calls in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py`
- [X] T035 [US3] Enforce campaign-scoped single-session lookup for session child endpoints, player-facing `/me/*` routes, and `GET /services/jdr/jobs/{job_id}` in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py`
- [X] T036 [US3] Enforce campaign-scoped session lookup in audio upload and purge routes in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\batch\router.py`
- [X] T037 [US3] Preserve existing API-key compatibility by resolving API-key requests to the V1 default campaign when no web user campaign is available in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\campaigns.py`
- [X] T038 [US3] Run focused campaign scoping tests in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_scoping.py`

**Checkpoint**: JDR root data and child routes respect campaign isolation without frontend-supplied `campaign_id`.

---

## Phase 6: User Story 4 - User management remains campaign-aware (Priority: P4)

**Goal**: Existing user-management contracts remain stable while created/listed/updated/deleted users stay consistent with the active campaign.

**Independent Test**: As a campaign MJ, create users through `/services/jdr/users`, verify membership creation and role mapping, list only users in the active campaign, update profile and sync role, then soft-delete while retaining membership rows.

### Tests for User Story 4

- [X] T039 [US4] Add tests proving `POST /services/jdr/users` creates exactly one campaign membership with role `mj` or `player` in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_user_campaign_membership.py`
- [X] T040 [US4] Add tests proving `GET /services/jdr/users` lists only users in the active campaign in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_user_campaign_membership.py`
- [X] T041 [US4] Add tests proving `PATCH /services/jdr/users/{user_id}` keeps membership role consistent with `profile` in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_user_campaign_membership.py`
- [X] T042 [US4] Add tests proving `DELETE /services/jdr/users/{user_id}` keeps `campaign_members` rows for auditability in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_user_campaign_membership.py`

### Implementation for User Story 4

- [X] T043 [US4] Extend `create_user`, `list_users`, `update_user`, and `delete_user` campaign-aware helper behavior without changing response schemas in `D:\Projets\dev\AI-Kaeyris\app\core\users.py`
- [X] T044 [US4] Resolve active campaign in `POST`, `GET`, `PATCH`, and `DELETE /services/jdr/users` routes and call membership helpers in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T045 [US4] Add membership role synchronization when a user's `profile` changes in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\campaigns.py`
- [X] T046 [US4] Keep soft-delete membership retention explicit and logged in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T047 [US4] Run focused campaign-aware user management tests in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_user_campaign_membership.py`

**Checkpoint**: Existing user CRUD contracts remain stable while campaign membership behavior is correct.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, OpenAPI output, migration validation, and full quality gate.

- [X] T048 [P] Update JDR service documentation with campaign context, `/auth/me`, and V1 out-of-scope campaign management notes in `D:\Projets\dev\AI-Kaeyris\docs\services\jdr.md`
- [X] T049 [P] Add ADR 0012 documenting campaign as the JDR multi-tenancy boundary and why campaign CRUD is out of scope in `D:\Projets\dev\AI-Kaeyris\docs\adr\0012-campaign-auth-context.md`
- [X] T050 [P] Add journal entry for BD-4 campaign context learnings in `D:\Projets\dev\AI-Kaeyris\docs\journal.md`
- [X] T051 [P] Update README setup/runtime notes for `/services/jdr/auth/me` and default campaign seed behavior in `D:\Projets\dev\AI-Kaeyris\README.md`
- [X] T052 Verify runtime `/openapi.json` includes `/services/jdr/auth/me`; generate or refresh `D:\Projets\dev\AI-Kaeyris\docs\context\api\openapi.json` only if the backend repo stores that artifact
- [X] T053 Run migration smoke test using `alembic upgrade head`, `alembic downgrade -1`, and `alembic upgrade head` from `D:\Projets\dev\AI-Kaeyris\alembic.ini`
- [X] T054 Run quickstart validation from `D:\Projets\dev\AI-Kaeyris\specs\004-campaign-auth-context\quickstart.md`
- [X] T055 Run full quality gate with `ruff check .` from `D:\Projets\dev\AI-Kaeyris\pyproject.toml`
- [X] T056 Run full test suite with `pytest` from `D:\Projets\dev\AI-Kaeyris\tests`
- [X] T057 Run `docker compose config` from `D:\Projets\dev\AI-Kaeyris\docker-compose.yml`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: no dependencies.
- **Phase 2 Foundational**: depends on Phase 1; blocks all user stories.
- **US1**: depends on Phase 2; delivers MVP `/auth/me` for users with memberships.
- **US2**: depends on Phase 2; can be implemented after or alongside US1, but is required for existing data and normal local startup.
- **US3**: depends on Phase 2 and benefits from US1/US2 active context helpers.
- **US4**: depends on Phase 2 and active context helpers; can be implemented after US1/US2.
- **Polish**: depends on all desired user stories being complete.

### User Story Dependencies

- **US1 (P1)**: MVP frontend contract for `/auth/me`; no dependency on US2 if tests seed memberships directly.
- **US2 (P2)**: Backfill/seed path for existing users; required before declaring BD-4 safe for real local data.
- **US3 (P3)**: Campaign isolation for JDR data; depends on active campaign context and campaign-owned root data.
- **US4 (P4)**: User management side effects; depends on campaign membership helpers.

### Within Each User Story

- Write tests first and verify they fail.
- Implement models/helpers before endpoints.
- Implement endpoint behavior before documentation.
- Run focused tests at each checkpoint before moving on.
- Preserve the V1 out-of-scope list: no campaign CRUD, no campaign switch endpoint, no tenant/org layer.

---

## Parallel Opportunities

- T002 and T003 can run in parallel after T001 starts.
- T004 and T005 can be written in parallel because they target different test files.
- T006 and T007 touch different model files but T008 must wait for both.
- T015 can be written in parallel with T013-T014 because it targets a different file.
- T021 and T022 can be written in parallel before US2 implementation.
- T027 through T030 are intentionally sequential because they target the same scoping test file.
- T039 through T042 are intentionally sequential because they target the same user-management membership test file.
- T048 through T051 can be updated in parallel after implementation stabilizes.

## Parallel Example: User Story 3

```text
Task: "T027 Add session creation/list isolation tests in tests/services/jdr/test_campaign_scoping.py"
Task: "T028 Add session child, /me/*, and jobs isolation tests in tests/services/jdr/test_campaign_scoping.py"
Task: "T029 Add PJ validation isolation tests in tests/services/jdr/test_campaign_scoping.py"
Task: "T030 Add rejected campaign_id body tests in tests/services/jdr/test_campaign_scoping.py"
```

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1 setup.
2. Complete Phase 2 foundational schema and active-campaign helpers.
3. Complete Phase 3 US1 `/auth/me`.
4. Stop and validate: `/auth/me` works for MJ, player, no campaign, and unauthenticated cases.

### Incremental Delivery

1. US1: frontend can consume live `/auth/me` with seeded memberships.
2. US2: existing users and startup seed produce memberships automatically.
3. US3: JDR sessions/PJs and child routes become campaign-scoped.
4. US4: user management creates/lists/updates/deletes with campaign membership side effects.
5. Polish: docs, ADR, OpenAPI, migration smoke, quickstart, full tests.

### Guardrails

- Do not add campaign CRUD endpoints in BD-4.
- Do not accept `campaign_id` from frontend request bodies.
- Do not remove or rename existing `profile` values in V1.
- Do not remove existing Bearer API-key compatibility.
- Do not physically delete users or campaign memberships during soft-delete.
- Do not introduce a tenant/organization abstraction.
