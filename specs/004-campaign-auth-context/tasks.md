# Tasks: Campaign Auth Context

**Input**: Design documents from `D:\Projets\dev\AI-Kaeyris\specs\004-campaign-auth-context\`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/rest-api.md`, `quickstart.md`

**Tests**: Required by project constitution. Write tests first for each public endpoint, migration/backfill rule, campaign membership rule, and campaign isolation rule.

**Organization**: Tasks are grouped by user story to enable independent implementation and validation.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete tasks)
- **[Story]**: User story label (`US1`, `US2`, `US3`, `US4`)
- Every task includes exact file paths

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Prepare a small campaign-context surface before model and route work.

- [X] T001 Create campaign context module scaffold with constants, typed result objects, and domain exceptions in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\campaign_context.py`
- [X] T002 [P] Add shared BD-4 fixture helpers for users, campaigns, memberships, sessions, and PJs in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\campaign_fixtures.py`
- [X] T003 [P] Add a brief BD-4 campaign-auth implementation note in `D:\Projets\dev\AI-Kaeyris\docs\services\jdr.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Database shape, schemas, and resolver primitives required by every user story.

**CRITICAL**: No user story work should start until this phase is complete.

### Tests for Foundational Work

- [X] T004 [P] Add campaign ORM and default-campaign resolution unit tests in `D:\Projets\dev\AI-Kaeyris\tests\core\test_campaign_context.py`
- [X] T005 [P] Add campaign repository and membership invariant tests in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_memberships.py`
- [X] T006 Add migration schema smoke tests for campaign tables and campaign_id columns in `D:\Projets\dev\AI-Kaeyris\tests\core\test_campaign_context.py`

### Implementation for Foundational Work

- [X] T007 Add `CampaignRole`, `Campaign`, `CampaignMember`, `Session.campaign_id`, and `Pj.campaign_id` ORM fields in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\models.py`
- [X] T008 Add `User.default_campaign_id` ORM field and relationship-safe typing in `D:\Projets\dev\AI-Kaeyris\app\core\models.py`
- [X] T009 Create Alembic migration `0006_campaign_auth_context.py` for campaign tables, `core_users.default_campaign_id`, `jdr_sessions.campaign_id`, `jdr_pjs.campaign_id`, indexes, FKs, and downgrade in `D:\Projets\dev\AI-Kaeyris\migrations\versions\0006_campaign_auth_context.py`
- [X] T010 Implement `CampaignRepository` and membership query helpers in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\repositories.py`
- [X] T011 Implement default-campaign creation, active-campaign resolution, and API-key fallback helpers in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\campaign_context.py`
- [X] T012 Extend authenticated identity with web user id support while preserving API-key behavior in `D:\Projets\dev\AI-Kaeyris\app\core\auth.py`
- [X] T013 Add `AuthMeUserOut`, `AuthMeCampaignOut`, and `AuthMeOut` response schemas in `D:\Projets\dev\AI-Kaeyris\app\core\user_schemas.py`
- [X] T014 Run foundational tests: `pytest tests/core/test_campaign_context.py tests/services/jdr/test_campaign_memberships.py`

**Checkpoint**: Campaign schema and active-campaign resolution primitives exist and are testable without exposing `/auth/me` yet.

---

## Phase 3: User Story 1 - Recuperer le contexte courant apres login (Priority: P1) MVP

**Goal**: A logged-in web user can call `GET /services/jdr/auth/me` and receive public user identity plus active campaign context.

**Independent Test**: Create a user, campaign, membership, and web session; call `/services/jdr/auth/me` with the cookie; verify the response shape, role vocabulary, `active_campaign: null` fallback, and 401 for invalid sessions.

### Tests for User Story 1

- [X] T015 [US1] Add `/services/jdr/auth/me` success contract tests for GM and player memberships in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_auth_me.py`
- [X] T016 [US1] Add `/services/jdr/auth/me` tests for no membership, expired session, revoked session, deleted user, and no secret exposure in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_auth_me.py`
- [X] T017 [US1] Add OpenAPI route smoke test for `GET /services/jdr/auth/me` in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_auth_me.py`

### Implementation for User Story 1

- [X] T018 [US1] Add `GET /services/jdr/auth/me` route with `AuthMeOut` response model in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T019 [US1] Ensure `/services/jdr/auth/me` validates the web session directly and returns 401 before campaign lookup when the session is invalid in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T020 [US1] Add `Cache-Control: no-store` to `/services/jdr/auth/me` responses in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T021 [US1] Run focused auth-me tests: `pytest tests/services/jdr/test_auth_me.py`

**Checkpoint**: Frontend can replace its `/auth/me` mock for users that already have memberships.

---

## Phase 4: User Story 2 - Rattacher les utilisateurs a la campagne V1 (Priority: P2)

**Goal**: Existing users, first-run setup users, and newly created users all receive a V1 default-campaign membership automatically.

**Independent Test**: Start from both empty and pre-existing-user databases; verify default campaign creation, user membership creation, `default_campaign_id`, setup behavior, user creation behavior, and logical-delete membership retention.

### Tests for User Story 2

- [X] T022 [P] [US2] Add tests proving first-run setup creates default campaign, GM membership, and `default_campaign_id` in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_auth_setup.py`
- [X] T023 [P] [US2] Add tests proving `POST /services/jdr/users` creates campaign membership, maps `gm -> gm` and `user -> player`, and falls back to the default campaign when the creator has no active campaign in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_memberships.py`
- [X] T024 [P] [US2] Add tests proving migration/adoption backfills existing users, sessions, and PJs to the default campaign in `D:\Projets\dev\AI-Kaeyris\tests\core\test_campaign_context.py`
- [X] T025 [US2] Add tests proving logical delete keeps membership rows while blocking future login in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_memberships.py`
- [X] T026 [US2] Add tests proving `GET /services/jdr/users` only lists users in the active campaign in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_memberships.py`

### Implementation for User Story 2

- [X] T027 [US2] Implement idempotent default campaign adoption/backfill helper in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\campaign_context.py`
- [X] T028 [US2] Update `POST /services/jdr/auth/setup` to create the default campaign, first GM membership, and user default campaign atomically in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T029 [US2] Update `POST /services/jdr/users` to add new users to the creator active/default campaign in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T030 [US2] Update `GET`, `PATCH`, and `DELETE /services/jdr/users` to enforce active-campaign membership scope in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T031 [US2] Implement migration data backfill for existing users, sessions, and PJs in `D:\Projets\dev\AI-Kaeyris\migrations\versions\0006_campaign_auth_context.py`
- [X] T032 [US2] Run focused membership/setup tests: `pytest tests/services/jdr/test_auth_setup.py tests/services/jdr/test_campaign_memberships.py tests/core/test_campaign_context.py`

**Checkpoint**: `/auth/me` works immediately after setup and after user creation, without manual SQL or seed scripts.

---

## Phase 5: User Story 3 - Isoler les donnees JDR par campagne active (Priority: P2)

**Goal**: JDR sessions, PJs, mappings, players, artifacts, jobs, and player `/me/*` reads are scoped to the authenticated active campaign.

**Independent Test**: Create two campaigns with data in each; authenticate as a member of campaign A; verify campaign B data is absent from lists and inaccessible through direct ids without revealing existence.

### Tests for User Story 3

- [X] T033 [US3] Add session create/list/detail/update isolation tests in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_isolation.py`
- [X] T034 [US3] Add PJ create/list/detail/update/delete isolation tests in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_isolation.py`
- [X] T035 [US3] Add mapping and non-diarised players cross-campaign validation tests in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_isolation.py`
- [X] T036 [US3] Add artifacts, audio, chunks, transcription, and jobs cross-campaign access tests in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_isolation.py`
- [X] T037 [US3] Add player `/services/jdr/me*` campaign-boundary tests in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_isolation.py`

### Implementation for User Story 3

- [X] T038 [US3] Update `SessionRepository.create`, `list_for_gm`, `get_for_gm`, and state/audio helpers to accept and enforce `campaign_id` in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\repositories.py`
- [X] T039 [US3] Update `PjRepository.create`, `list_for_gm`, and `find_by_id_owned_by` to accept and enforce `campaign_id` in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\repositories.py`
- [X] T040 [US3] Update JDR business logic to pass campaign scope into session and PJ workflows in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\logic.py`
- [X] T041 [US3] Update top-level JDR routes to resolve active campaign scope before GM operations in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py`
- [X] T042 [US3] Update batch audio routes to reject out-of-campaign sessions in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\batch\router.py`
- [X] T043 [US3] Update mapping and non-diarised players validation to require session and PJ campaign match in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py`
- [X] T044 [US3] Update artifact generation/read routes and job lookup routes to use campaign-scoped session lookup in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py`
- [X] T045 [US3] Update player `/services/jdr/me*` routes to ensure player character/session access stays within one campaign in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py`
- [X] T046 [US3] Review JDR background job entrypoints so jobs loaded by `session_id` cannot be enqueued from an out-of-campaign route in `D:\Projets\dev\AI-Kaeyris\app\jobs\jdr.py`
- [X] T047 [US3] Run focused isolation tests: `pytest tests/services/jdr/test_campaign_isolation.py`

**Checkpoint**: A user scoped to campaign A cannot see or operate on campaign B JDR data through existing endpoints.

---

## Phase 6: User Story 4 - Preserver les contrats web existants (Priority: P3)

**Goal**: Existing setup, login, logout, user-management, API-key, and OpenAPI contracts remain stable while BD-4 adds campaign context.

**Independent Test**: Existing auth/user tests continue to pass; existing request bodies do not gain `campaign_id`; API-key clients still authenticate; OpenAPI includes `/auth/me`.

### Tests for User Story 4

- [X] T048 [P] [US4] Add regression tests proving login, logout, setup, and user CRUD request bodies remain campaign-free in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_auth_login.py`
- [X] T049 [P] [US4] Add regression tests proving existing API-key GM operations resolve a safe default campaign in `D:\Projets\dev\AI-Kaeyris\tests\core\test_auth_roles.py`
- [X] T050 [P] [US4] Add OpenAPI regression test proving `/services/jdr/auth/me` is present and existing auth paths remain present in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_auth_me.py`

### Implementation for User Story 4

- [X] T051 [US4] Preserve exact login invalid-credential and unsupported-profile Problem Details bodies in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T052 [US4] Preserve API-key authentication precedence over session cookie while adding campaign fallback in `D:\Projets\dev\AI-Kaeyris\app\core\auth.py`
- [X] T053 [US4] Update route summaries/descriptions for `/services/jdr/auth/me` without changing existing auth path semantics in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T054 [US4] Run focused regression tests: `pytest tests/services/jdr/test_auth_login.py tests/services/jdr/test_auth_logout.py tests/services/jdr/test_user_management.py tests/core/test_auth_roles.py tests/services/jdr/test_auth_me.py`

**Checkpoint**: BD-4 adds context without regressing the delivered web-auth feature from `003-user-password-auth`.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, validation, and full quality gate.

- [X] T055 [P] Update service auth/campaign documentation in `D:\Projets\dev\AI-Kaeyris\docs\services\jdr.md`
- [X] T056 [P] Update quick command references for `/auth/me` and campaign validation in `D:\Projets\dev\AI-Kaeyris\docs\memo.md`
- [X] T057 [P] Add ADR for campaign auth context and V1 default-campaign strategy in `D:\Projets\dev\AI-Kaeyris\docs\adr\0012-campaign-auth-context.md`
- [X] T058 [P] Add journal entry for BD-4 campaign context learnings in `D:\Projets\dev\AI-Kaeyris\docs\journal.md`
- [X] T059 Update README auth/JDR sections with `/services/jdr/auth/me` and campaign-scoping behavior in `D:\Projets\dev\AI-Kaeyris\README.md`
- [X] T060 Run migration smoke test for `D:\Projets\dev\AI-Kaeyris\migrations\versions\0006_campaign_auth_context.py`: `alembic upgrade head`, `alembic downgrade -1`, `alembic upgrade head`
- [X] T061 Run quickstart validation from `D:\Projets\dev\AI-Kaeyris\specs\004-campaign-auth-context\quickstart.md`
- [X] T062 Run full quality gate from `D:\Projets\dev\AI-Kaeyris`: `ruff check .`
- [X] T063 Run full test suite from `D:\Projets\dev\AI-Kaeyris`: `pytest`
- [X] T064 Verify Docker Compose config still renders from `D:\Projets\dev\AI-Kaeyris\docker-compose.yml`: `docker compose config`
- [ ] T065 Run lightweight local latency smoke check for `/services/jdr/auth/me` and campaign-scoped list reads from `D:\Projets\dev\AI-Kaeyris\specs\004-campaign-auth-context\quickstart.md`
- [ ] T066 Run Docker Compose stack smoke test and manually validate `GET /services/jdr/auth/me` from `D:\Projets\dev\AI-Kaeyris`: `docker compose up`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: no dependencies.
- **Phase 2 Foundational**: depends on Phase 1; blocks every user story.
- **US1 (P1)**: depends on Phase 2 and delivers the MVP `/auth/me` contract for users with memberships.
- **US2 (P2)**: depends on Phase 2 and makes memberships/default campaign automatic for setup, existing users, and new users.
- **US3 (P2)**: depends on Phase 2 and should run after US2 for realistic data, though its isolation tests can seed memberships directly.
- **US4 (P3)**: depends on US1-US3 because it verifies BD-4 did not regress prior contracts.
- **Polish**: depends on the desired user stories being complete.

### User Story Dependencies

- **US1 (P1)**: Minimal runtime contract; can be tested with fixture-created memberships.
- **US2 (P2)**: Makes campaign membership automatic; needed for real setup/login flows.
- **US3 (P2)**: Uses the campaign model and membership context to scope JDR data.
- **US4 (P3)**: Regression and compatibility story; best executed after functional stories.

### Within Each User Story

- Write tests first and verify they fail.
- Implement model/schema changes before repositories.
- Implement repositories/resolvers before endpoints.
- Implement endpoints before docs and quickstart validation.
- Run focused tests at each checkpoint before moving on.

---

## Parallel Opportunities

- T002 and T003 can run in parallel after T001 starts.
- T004 and T005 can be written in parallel before foundational implementation; T006 follows T004 because both edit `tests/core/test_campaign_context.py`.
- T022, T023, and T024 can be written in parallel across setup, membership, and migration scenarios.
- T033 through T037 are intentionally sequential because they share `test_campaign_isolation.py`; split them into separate files first if parallel authoring becomes useful.
- T048 through T050 can be written in parallel for regression coverage.
- T055 through T058 can be updated in parallel after behavior stabilizes.

## Parallel Example: User Story 4

```text
Task: "T048 Add regression tests proving login, logout, setup, and user CRUD request bodies remain campaign-free in tests/services/jdr/test_auth_login.py"
Task: "T049 Add regression tests proving existing API-key GM operations resolve a safe default campaign in tests/core/test_auth_roles.py"
Task: "T050 Add OpenAPI regression test proving /services/jdr/auth/me is present and existing auth paths remain present in tests/services/jdr/test_auth_me.py"
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1 setup and Phase 2 foundational schema/resolver work.
2. Complete Phase 3 US1 `/services/jdr/auth/me`.
3. Stop and validate with `pytest tests/services/jdr/test_auth_me.py`.
4. At this point, the front can test the live endpoint with fixture/manual memberships, but setup-created memberships still require US2.

### Incremental Delivery

1. US1: current-context endpoint exists and is safe.
2. US2: setup/user creation/backfill make memberships automatic.
3. US3: JDR data is scoped by active campaign.
4. US4: existing auth and API-key contracts are proven stable.
5. Polish: docs, ADR, quickstart, full quality gate.

### Guardrails

- Do not add campaign CRUD, campaign switching, tenants, organizations, OAuth, JWT, or a frontend UI.
- Do not expose `campaign_id` in create request bodies.
- Do not let client input override authenticated campaign scope.
- Do not remove existing API-key auth.
- Do not physically delete users or membership audit rows.
- Do not expose password hashes, token hashes, session tokens, or internal API-key hashes.
