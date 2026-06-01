# Tasks: Identity Refactor and PJ Campaign Scoping

**Input**: Design documents from `D:\Projets\dev\AI-Kaeyris\specs\007-identity-refacto-pj-scoping\`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/rest-api.md`, `quickstart.md`

**Tests**: Required by the project constitution. Write endpoint, schema, migration/reseed, authorization, OpenAPI, and regression tests before implementation.

**Organization**: Tasks are grouped by user story to enable independent implementation and validation.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete tasks)
- **[Story]**: User story label (`US1`, `US2`, `US3`, `US4`, `US5`)
- Every task includes exact file paths

---

## Phase 1: Setup (Shared Context)

**Purpose**: Confirm BD-7 starts from merged BD-6 and identify all shared identity/PJ surfaces before changing contracts.

- [X] T001 Review current `Profile`, `User`, and web-session identity model in `D:\Projets\dev\AI-Kaeyris\app\core\models.py`, `D:\Projets\dev\AI-Kaeyris\app\core\users.py`, and `D:\Projets\dev\AI-Kaeyris\app\core\user_schemas.py`
- [X] T002 [P] Review current campaign membership role helpers and default campaign behavior in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\campaign_context.py`
- [X] T003 [P] Review current PJ ORM, repository, logic, and router behavior in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\models.py`, `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\repositories.py`, `D:\Projets\dev\AI-Kaeyris\app\services\jdr\logic.py`, and `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py`
- [X] T004 [P] Review current auth/user-management endpoint tests to identify `profile` and `player` expectations in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_auth_me.py`, `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_user_management.py`, and `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_memberships.py`
- [X] T005 [P] Review current PJ and player-token regression tests in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_pjs.py`, `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_player_access.py`, and `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_player_listing.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared schema, vocabulary, fixtures, and contract primitives required by every user story.

**CRITICAL**: No user story work should start until this phase is complete.

### Tests for Foundational Work

- [X] T006 [P] Add model/schema tests for `User.system_role`, absence of public `profile`, `CampaignMember.role` values `gm|pj`, required `Pj.campaign_id`, and nullable `Pj.user_id` in `D:\Projets\dev\AI-Kaeyris\tests\core\test_identity_refactor.py`
- [X] T007 [P] Add migration/reseed tests for the purge-oriented BD-7 schema and default admin/campaign/GM membership setup in `D:\Projets\dev\AI-Kaeyris\tests\core\test_identity_reseed.py`
- [X] T008 [P] Add datetime serialization regression coverage for new/changed user and PJ outputs in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_datetime_serialization.py`
- [X] T009 [P] Update BD-7 fixture helpers for users, campaigns, memberships, and PJs in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\campaign_fixtures.py`

### Implementation for Foundational Work

- [X] T010 Add Alembic migration `0008_identity_refacto_pj_scoping.py` for `system_role`, `gm|pj` membership role values, required PJ campaign, and nullable PJ user assignment in `D:\Projets\dev\AI-Kaeyris\migrations\versions\0008_identity_refacto_pj_scoping.py`
- [X] T011 Replace public `Profile` account semantics with `SystemRole` or equivalent `admin|user` model in `D:\Projets\dev\AI-Kaeyris\app\core\models.py`
- [X] T012 Update user creation, authentication, default campaign, and last-admin guard logic for `system_role` in `D:\Projets\dev\AI-Kaeyris\app\core\users.py`
- [X] T013 Update `SetupRequest`, `LoginRequest`, `UserCreate`, `UserUpdate`, `UserOut`, `AuthMeUserOut`, and related schemas to expose `system_role` instead of `profile` in `D:\Projets\dev\AI-Kaeyris\app\core\user_schemas.py`
- [X] T014 Update campaign membership enum/value helpers from `player` to `pj` while preserving legacy API-key player-token semantics where still required in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\models.py` and `D:\Projets\dev\AI-Kaeyris\app\services\jdr\campaign_context.py`
- [X] T015 Update `Pj` ORM fields and relationships for required `campaign_id` and nullable `user_id` in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\models.py`
- [X] T016 Update repository methods that read/write users, memberships, campaigns, and PJs for the new identity and PJ model in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\repositories.py`
- [X] T017 Update JDR schemas for campaign role `gm|pj`, `PjCreate.campaign_id`, `PjCreate.user_id`, `PjOut.campaign_id`, and `PjOut.user_id` in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\schemas.py`
- [X] T018 Run foundational tests from `D:\Projets\dev\AI-Kaeyris`: `uv run pytest tests\core\test_identity_refactor.py tests\core\test_identity_reseed.py tests\services\jdr\test_datetime_serialization.py -q`

**Checkpoint**: Shared vocabulary, schema, fixtures, and serializers are ready for endpoint behavior changes.

---

## Phase 3: User Story 1 - Separate System Admin From Campaign GM (Priority: P1) MVP

**Goal**: Only global admins can manage user accounts, while standard users can still create and GM campaigns.

**Independent Test**: Create one admin and one standard user, verify `/users/*` rejects the standard user and accepts the admin, then verify the standard user can create a campaign and becomes campaign GM.

### Tests for User Story 1

- [X] T019 [P] [US1] Add user-management authorization tests for admin-only `GET/POST/PATCH/DELETE /services/jdr/users` in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_user_management.py`
- [X] T020 [P] [US1] Add standard-user campaign creation tests proving no admin role is required in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaigns_crud.py`
- [X] T021 [P] [US1] Add OpenAPI tests proving user schemas expose `system_role` and not `profile` in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_user_management.py`

### Implementation for User Story 1

- [X] T022 [US1] Add admin-required authorization helper or predicate for user-management routes in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T023 [US1] Apply admin-only checks to `GET/POST/PATCH/DELETE /services/jdr/users` in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T024 [US1] Ensure `POST /services/jdr/campaigns` accepts any authenticated user and creates GM membership in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\logic.py` and `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py`
- [X] T025 [US1] Map non-admin user-management attempts to a clear admin-required Problem Details response in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T026 [US1] Run US1 tests from `D:\Projets\dev\AI-Kaeyris`: `uv run pytest tests\services\jdr\test_user_management.py tests\services\jdr\test_campaigns_crud.py -q`

**Checkpoint**: Portal administration is separated from campaign GM authority.

---

## Phase 4: User Story 2 - Rename Campaign Player Role To PJ (Priority: P1)

**Goal**: Public campaign membership and current identity responses use `pj`, never `player`.

**Independent Test**: Assign a user to a campaign as PJ, fetch current identity and campaign/member-derived responses, and verify public role values are `gm|pj`.

### Tests for User Story 2

- [X] T027 [P] [US2] Update `/auth/me` tests to expect active campaign role `pj` for PJ members in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_auth_me.py`
- [X] T028 [P] [US2] Add campaign membership tests proving stored/public membership role values are `gm|pj` and never `player` in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_memberships.py`
- [X] T029 [P] [US2] Add OpenAPI/schema tests for campaign role enum values in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_campaign_memberships.py`

### Implementation for User Story 2

- [X] T030 [US2] Update `CampaignRole` enum and any membership role serialization to use `PJ = "pj"` in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\models.py`
- [X] T031 [US2] Update campaign role helpers and active campaign resolution to return `pj` for PJ members in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\campaign_context.py`
- [X] T032 [US2] Update auth/me response construction and membership responses to emit `pj` in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T033 [US2] Update tests and fixtures still using `CampaignRole.PLAYER` for web campaign membership to `CampaignRole.PJ` in `D:\Projets\dev\AI-Kaeyris\tests\`
- [X] T034 [US2] Run US2 tests from `D:\Projets\dev\AI-Kaeyris`: `uv run pytest tests\services\jdr\test_auth_me.py tests\services\jdr\test_campaign_memberships.py -q`

**Checkpoint**: Web campaign role vocabulary is aligned with JDR "PJ" terminology.

---

## Phase 5: User Story 3 - Scope PJs To Campaigns (Priority: P1)

**Goal**: PJs belong to campaigns, can optionally be assigned to users, and PJ list/create behavior is campaign-aware while preserving V1 fallback.

**Independent Test**: Create PJs across two campaigns and verify explicit create, default fallback create, unfiltered member list, filtered member list, non-member rejection, and non-GM create rejection.

### Tests for User Story 3

- [X] T035 [P] [US3] Add `POST /services/jdr/pjs` tests for explicit campaign, default-campaign fallback, missing fallback failure, optional `user_id`, and non-GM rejection in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_pjs.py`
- [X] T036 [P] [US3] Add `GET /services/jdr/pjs` tests for unfiltered member campaigns, filtered campaign, non-member rejection, and empty result in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_pjs.py`
- [X] T037 [P] [US3] Add PJ OpenAPI tests for optional create `campaign_id`, optional create `user_id`, required output `campaign_id`, and optional output `user_id` in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_pjs.py`
- [X] T038 [P] [US3] Update player-token and player-access regression tests impacted by PJ campaign scoping in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_player_access.py`, `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_player_listing.py`, and `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_player_enroll.py`

### Implementation for User Story 3

- [X] T039 [US3] Update `PjRepository.create`, `list_for_gm`, and lookup methods for campaign membership visibility and default-campaign fallback in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\db\repositories.py`
- [X] T040 [US3] Update PJ create/list business logic to resolve explicit or default campaign and require GM membership for create in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\logic.py`
- [X] T041 [US3] Update `POST /services/jdr/pjs` to accept optional `campaign_id` and `user_id`, return `campaign_id` and `user_id`, and map missing fallback/non-GM errors in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py`
- [X] T042 [US3] Update `GET /services/jdr/pjs` to accept optional `campaign_id`, enforce membership, and return all member-campaign PJs when omitted in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\router.py`
- [X] T043 [US3] Update session mapping/player-list logic so campaign-scoped PJ validation still works for diarised and non_diarised flows in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\logic.py`
- [X] T044 [US3] Run US3 tests from `D:\Projets\dev\AI-Kaeyris`: `uv run pytest tests\services\jdr\test_pjs.py tests\services\jdr\test_player_access.py tests\services\jdr\test_player_listing.py tests\services\jdr\test_player_enroll.py -q`

**Checkpoint**: PJ public endpoints are campaign-aware and compatible with existing V1 PJ frontend behavior.

---

## Phase 6: User Story 4 - Expose Current Identity For The Frontend (Priority: P1)

**Goal**: `/auth/me` gives the frontend enough role information in one request to distinguish admin, standard user, campaign GM, and campaign PJ capabilities.

**Independent Test**: Sign in as admin, standard GM, and PJ member; verify `user.system_role`, active campaign role, and character id behavior.

### Tests for User Story 4

- [X] T045 [P] [US4] Add `/auth/me` tests for admin user, standard user, GM campaign member, PJ campaign member, and no active campaign in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_auth_me.py`
- [X] T046 [P] [US4] Add auth request/response OpenAPI tests proving auth bodies remain campaign-free and current identity exposes `system_role` in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_auth_login.py`

### Implementation for User Story 4

- [X] T047 [US4] Update `AuthMeUserOut`, `AuthMeCampaignOut`, and `AuthMeOut` schemas for `system_role` and `gm|pj` in `D:\Projets\dev\AI-Kaeyris\app\core\user_schemas.py`
- [X] T048 [US4] Update `/services/jdr/auth/me` route to return `system_role` and new campaign role vocabulary in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T049 [US4] Ensure setup/login responses and cookies keep existing behavior while user public data uses `system_role` in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T050 [US4] Run US4 tests from `D:\Projets\dev\AI-Kaeyris`: `uv run pytest tests\services\jdr\test_auth_me.py tests\services\jdr\test_auth_login.py -q`

**Checkpoint**: Frontend can derive global and campaign permissions from current identity.

---

## Phase 7: User Story 5 - Start From A Clean Reseed (Priority: P2)

**Goal**: A purged local/staging environment can be rebuilt with one admin, one default campaign, and GM membership without manual DB edits.

**Independent Test**: Start from an empty DB, apply migrations/reseed path, sign in as admin, and verify default campaign GM context.

### Tests for User Story 5

- [X] T051 [P] [US5] Add empty-database setup/reseed tests for admin, default campaign, GM membership, and default campaign assignment in `D:\Projets\dev\AI-Kaeyris\tests\core\test_identity_reseed.py`
- [X] T052 [P] [US5] Add security regression tests proving no production hardcoded admin credential is silently enabled in `D:\Projets\dev\AI-Kaeyris\tests\core\test_identity_reseed.py`

### Implementation for User Story 5

- [X] T053 [US5] Implement explicit local/staging reseed helper or setup flow for one admin, default campaign, and GM membership in `D:\Projets\dev\AI-Kaeyris\app\core\users.py` and `D:\Projets\dev\AI-Kaeyris\app\services\jdr\campaign_context.py`
- [X] T054 [US5] Document purge/reseed operator steps in `D:\Projets\dev\AI-Kaeyris\docs\services\jdr.md` and `D:\Projets\dev\AI-Kaeyris\docs\memo.md`
- [X] T055 [US5] Run US5 tests from `D:\Projets\dev\AI-Kaeyris`: `uv run pytest tests\core\test_identity_reseed.py -q`

**Checkpoint**: BD-7 can be validated from a clean local/staging database without hidden production credentials.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, contract verification, regression coverage, and full quality gate.

- [X] T056 [P] Update JDR service documentation for `system_role`, `gm|pj`, PJ campaign scoping, and V1 PJ fallback in `D:\Projets\dev\AI-Kaeyris\docs\services\jdr.md`
- [X] T057 [P] Update command/reference memo for BD-7 auth and PJ workflows in `D:\Projets\dev\AI-Kaeyris\docs\memo.md`
- [X] T058 [P] Add journal entry for BD-7 identity separation and PJ scoping in `D:\Projets\dev\AI-Kaeyris\docs\journal.md`
- [X] T059 [P] Update README user/auth/JDR examples to use `system_role`, `gm|pj`, and PJ campaign scoping in `D:\Projets\dev\AI-Kaeyris\README.md`
- [X] T060 [P] Regenerate or export OpenAPI contract for frontend sync in `D:\Projets\dev\AI-Kaeyris\docs\context\api\openapi.json` if this artifact is owned by the backend repo
- [X] T061 Add ADR for identity role separation and PJ campaign scoping in `D:\Projets\dev\AI-Kaeyris\docs\adr\0013-identity-refacto-pj-scoping.md`
- [X] T062 Run quickstart validation commands from `D:\Projets\dev\AI-Kaeyris\specs\007-identity-refacto-pj-scoping\quickstart.md`
- [X] T063 Run full linter from `D:\Projets\dev\AI-Kaeyris`: `uv run ruff check .`
- [X] T064 Run full test suite from `D:\Projets\dev\AI-Kaeyris`: `uv run pytest -q`
- [X] T065 Verify Docker Compose config still renders from `D:\Projets\dev\AI-Kaeyris\docker-compose.yml`: `docker compose config`
- [X] T066 Verify `/openapi.json` exposes `system_role`, `gm|pj`, PJ `campaign_id`, optional PJ `user_id`, and no public `profile` field from `D:\Projets\dev\AI-Kaeyris`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: no dependencies.
- **Phase 2 Foundational**: depends on Phase 1; blocks every user story.
- **US1 (P1)**: depends on Phase 2 and delivers the core admin-vs-GM separation.
- **US2 (P1)**: depends on Phase 2 and can proceed alongside US1 if files are coordinated.
- **US3 (P1)**: depends on Phase 2; safest after US2 because it uses `gm|pj` campaign membership vocabulary.
- **US4 (P1)**: depends on US1/US2 vocabulary and can be validated independently once schemas are updated.
- **US5 (P2)**: depends on foundational schema decisions; can be done after the core P1 behavior is stable.
- **Polish**: depends on all desired user stories being complete.

### User Story Dependencies

- **US1 (P1)**: MVP for separating global account administration from campaign GM authority.
- **US2 (P1)**: Required vocabulary alignment for frontend role checks.
- **US3 (P1)**: Required PJ scoping for upcoming frontend PJ stories; depends on campaign role helpers.
- **US4 (P1)**: Requires the new role fields to be stable before current identity response is finalized.
- **US5 (P2)**: Completes purge/reseed validation after schema and roles are stable.

### Within Each User Story

- Write tests first and verify they fail.
- Update schemas and enums before route logic that exposes them.
- Update repositories before business logic functions.
- Update logic before route handlers.
- Run focused tests at each checkpoint before moving to the next phase.

---

## Parallel Opportunities

- T002 through T005 can run in parallel after T001 starts.
- T006 through T009 can be written in parallel because they target different test/helper files.
- T019 through T021 can be written in parallel for US1; T022 through T025 share `auth_router.py` and should be sequential.
- T027 through T029 can be written in parallel for US2; T030 through T033 touch shared role vocabulary and should be coordinated.
- T035 through T038 can be written in parallel for US3; T039 through T043 should be sequential across repository, logic, and router.
- T045 and T046 can run in parallel for US4.
- T051 and T052 can run in parallel for US5.
- T056 through T060 can run in parallel after behavior is stable.

## Parallel Example: Foundational Tests

```text
Task: "T006 Add model/schema tests for User.system_role and Pj campaign fields in tests/core/test_identity_refactor.py"
Task: "T007 Add migration/reseed tests in tests/core/test_identity_reseed.py"
Task: "T008 Add datetime serialization regression coverage in tests/services/jdr/test_datetime_serialization.py"
Task: "T009 Update BD-7 fixture helpers in tests/services/jdr/campaign_fixtures.py"
```

## Parallel Example: User Story 3

```text
Task: "T035 Add POST /services/jdr/pjs campaign-scoped tests in tests/services/jdr/test_pjs.py"
Task: "T036 Add GET /services/jdr/pjs campaign filter tests in tests/services/jdr/test_pjs.py"
Task: "T038 Update player-token regression tests in tests/services/jdr/test_player_access.py and related files"
```

---

## Implementation Strategy

### MVP First

1. Complete Phase 1 and Phase 2.
2. Complete US1 admin-vs-GM separation.
3. Validate that standard users can create campaigns while user management remains admin-only.

### Incremental Delivery

1. US1: global `system_role` behavior and admin-only account management.
2. US2: campaign role vocabulary `gm|pj`.
3. US3: campaign-scoped PJs with V1 fallback.
4. US4: current identity response for frontend permission decisions.
5. US5: purge/reseed validation.
6. Polish and full quality gates.

### Safety Notes

- Do not commit production credentials or universal admin defaults.
- Do not broaden BD-7 into fine-grained RBAC, invitation flows, audit logs, or cross-campaign PJ inheritance.
- Preserve legacy API-key player-token behavior unless a task explicitly proves a safe migration path.
- Keep every public datetime response under the BD-5 explicit timezone contract.
- Treat the purge/reseed approach as local/staging scope, not a production data migration promise.
