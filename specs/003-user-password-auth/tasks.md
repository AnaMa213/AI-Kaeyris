# Tasks: User Password Authentication

**Input**: Design documents from `specs/003-user-password-auth/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/rest-api.md`, `quickstart.md`

**Tests**: Required by project constitution. Write tests first for each public endpoint and non-trivial auth/session logic.

**Organization**: Tasks are grouped by user story to enable independent implementation and validation.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete tasks)
- **[Story]**: User story label (`US1`, `US2`, `US3`, `US4`)
- Every task includes exact file paths

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Prepare the shared auth surface without changing behavior yet.

- [X] T001 Add web auth settings (`SESSION_COOKIE_NAME`, `SESSION_COOKIE_SECURE`, `SESSION_COOKIE_SAMESITE`, `WEB_SESSION_TTL_SECONDS`) in `D:\Projets\dev\AI-Kaeyris\app\core\config.py`
- [X] T002 [P] Document the new web auth env vars with safe placeholder values in `D:\Projets\dev\AI-Kaeyris\.env.example`
- [X] T003 [P] Document first-run setup assumptions in `D:\Projets\dev\AI-Kaeyris\README.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Database entities and shared helpers required by all user stories.

**CRITICAL**: No user story work should start until this phase is complete.

- [X] T004 Add `Profile`, `UserStatus`, `User`, and `WebSession` ORM models in `D:\Projets\dev\AI-Kaeyris\app\core\models.py`
- [X] T005 Update Alembic model discovery to import core models in `D:\Projets\dev\AI-Kaeyris\migrations\env.py`
- [X] T006 Create Alembic migration `0005_user_password_auth.py` for `core_users` and `core_web_sessions` in `D:\Projets\dev\AI-Kaeyris\migrations\versions\0005_user_password_auth.py`
- [X] T007 [P] Add Pydantic schemas for login, user public output, user create/update, and user list in `D:\Projets\dev\AI-Kaeyris\app\core\user_schemas.py`
- [X] T008 Create user/session repository helpers in `D:\Projets\dev\AI-Kaeyris\app\core\users.py`
- [X] T009 Add unit tests for password hashing, username normalization, and session token hashing in `D:\Projets\dev\AI-Kaeyris\tests\core\test_user_auth.py`
- [X] T010 Add unit tests for session validity states (active, expired, revoked, deleted user) in `D:\Projets\dev\AI-Kaeyris\tests\core\test_web_sessions.py`
- [X] T011 Implement password verification, session token creation, token hashing, and session validation helpers in `D:\Projets\dev\AI-Kaeyris\app\core\users.py`

**Checkpoint**: Core user/session storage exists and helper tests pass.

---

## Phase 3: User Story 1 - Creation de profil puis login web (Priority: P1) MVP

**Goal**: A GM creates a profile with `username + profile + password`; that profile logs in, receives an HTTP-only session cookie, and can access protected routes with the cookie.

**Independent Test**: On an empty users table, call first-run setup to create the first GM, call `POST /services/jdr/users` to create a profile, call `POST /services/jdr/auth/login`, verify `200` + cookie, then access a protected route with cookie only.

### Tests for User Story 1

- [X] T012 [P] [US1] Add first-run setup, profile creation, and login contract tests in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_auth_login.py`
- [X] T013 [P] [US1] Add integration test proving a valid session cookie authenticates a protected route in `D:\Projets\dev\AI-Kaeyris\tests\core\test_web_sessions.py`
- [X] T014 [P] [US1] Add regression test proving an API key token is not accepted as a web password in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_auth_login.py`

### Implementation for User Story 1

- [X] T015 [US1] Extend `AuthenticatedKey` with auth source support while preserving API-key behavior in `D:\Projets\dev\AI-Kaeyris\app\core\auth.py`
- [X] T016 [US1] Teach `require_api_key` to accept `Authorization: Bearer` first and `session` cookie second in `D:\Projets\dev\AI-Kaeyris\app\core\auth.py`
- [X] T017 [US1] Create public auth router with `GET/POST /services/jdr/auth/setup`, `POST /services/jdr/auth/login`, and GM-only `POST /services/jdr/users` in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T018 [US1] Mount the public auth router outside the protected JDR router in `D:\Projets\dev\AI-Kaeyris\app\main.py`
- [X] T019 [US1] Add CORS middleware with explicit allowed origins and credentials support in `D:\Projets\dev\AI-Kaeyris\app\main.py`
- [X] T020 [US1] Add structured setup, profile creation, and login success/failure logs without secrets in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T021 [US1] Run focused MVP tests for profile creation, login, and session-cookie auth: `pytest tests/services/jdr/test_auth_login.py tests/core/test_web_sessions.py`

**Checkpoint**: MVP supports profile creation, login, cookie issuance, and cookie-authenticated protected calls.

---

## Phase 4: User Story 2 - Gestion complete des utilisateurs par un GM (Priority: P2)

**Goal**: A GM can list, update, and logically delete users after the MVP profile creation path exists.

**Independent Test**: Login as GM, use an existing `user`, change password/profile, verify behavior, logically delete, verify login is refused.

### Tests for User Story 2

- [X] T022 [P] [US2] Add user listing tests including no password hash exposure in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_user_management.py`
- [X] T023 [P] [US2] Add user update tests for password rotation and profile change in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_user_management.py`
- [X] T024 [P] [US2] Add logical delete tests proving deleted users cannot login and still appear as deleted in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_user_management.py`
- [X] T025 [P] [US2] Add authorization tests proving non-GM web users cannot manage users in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_user_management.py`
- [X] T026 [P] [US2] Add last-active-GM guard tests for PATCH and DELETE in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_user_management.py`

### Implementation for User Story 2

- [X] T027 [US2] Extend user service functions with list/update/logical-delete in `D:\Projets\dev\AI-Kaeyris\app\core\users.py`
- [X] T028 [US2] Add `GET`, `PATCH`, and `DELETE /services/jdr/users` routes in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T029 [US2] Add app errors for duplicate user, user not found, and last GM guard in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T030 [US2] Ensure user responses never serialize `password_hash`, token hashes, or session secrets in `D:\Projets\dev\AI-Kaeyris\app\core\user_schemas.py`
- [X] T031 [US2] Revoke active web sessions when a user is logically deleted in `D:\Projets\dev\AI-Kaeyris\app\core\users.py`
- [X] T032 [US2] Run focused tests for user management: `pytest tests/services/jdr/test_user_management.py tests/services/jdr/test_auth_login.py`

**Checkpoint**: GM can list, update, and logically delete users without manual DB edits.

---

## Phase 5: User Story 3 - Premiere initialisation via le front (Priority: P3)

**Goal**: A fresh install can create the first GM from the front, while web login no longer treats API keys as passwords.

**Independent Test**: Start with empty `core_users`, verify setup status is required, call setup to create first GM, verify setup is closed afterwards, login as first GM, verify API token-as-password is refused.

### Tests for User Story 3

- [X] T033 [P] [US3] Add setup status and setup-closed tests for empty and non-empty users table in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_auth_setup.py`
- [X] T034 [P] [US3] Add concurrency-style test proving setup re-checks emptiness before creating the first GM in `D:\Projets\dev\AI-Kaeyris\tests\core\test_user_setup.py`

### Implementation for User Story 3

- [X] T035 [US3] Implement first-run setup helpers in `D:\Projets\dev\AI-Kaeyris\app\core\users.py`
- [X] T036 [US3] Ensure `POST /services/jdr/auth/setup` creates the first GM and session atomically in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T037 [US3] Update first-run setup documentation in `D:\Projets\dev\AI-Kaeyris\README.md`
- [X] T038 [US3] Update auth quick references in `D:\Projets\dev\AI-Kaeyris\docs\memo.md`
- [X] T039 [US3] Run focused setup tests: `pytest tests/services/jdr/test_auth_setup.py tests/core/test_user_setup.py`

**Checkpoint**: An empty deployment can create its first GM from the front without default credentials.

---

## Phase 6: User Story 4 - Logout et expiration de session (Priority: P4)

**Goal**: A user can logout explicitly, and expired sessions are rejected.

**Independent Test**: Login, access a protected route, logout, verify same cookie fails; create an expired session and verify it is rejected.

### Tests for User Story 4

- [X] T040 [P] [US4] Add logout endpoint tests for success, cookie expiry header, and subsequent rejection in `D:\Projets\dev\AI-Kaeyris\tests\services\jdr\test_auth_logout.py`
- [X] T041 [P] [US4] Add configurable session TTL and expired-session rejection tests in `D:\Projets\dev\AI-Kaeyris\tests\core\test_web_sessions.py`

### Implementation for User Story 4

- [X] T042 [US4] Implement current-session revocation helper in `D:\Projets\dev\AI-Kaeyris\app\core\users.py`
- [X] T043 [US4] Add `POST /services/jdr/auth/logout` in `D:\Projets\dev\AI-Kaeyris\app\services\jdr\auth_router.py`
- [X] T044 [US4] Ensure expired and revoked sessions are rejected by cookie auth in `D:\Projets\dev\AI-Kaeyris\app\core\auth.py`
- [X] T045 [US4] Run focused logout/expiry tests: `pytest tests/services/jdr/test_auth_logout.py tests/core/test_web_sessions.py`

**Checkpoint**: Web sessions are bounded and explicitly revocable.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, manual validation, and full quality gate.

- [X] T046 [P] Update service auth documentation in `D:\Projets\dev\AI-Kaeyris\docs\services\jdr.md`
- [X] T047 [P] Add journal entry for user/password auth learnings in `D:\Projets\dev\AI-Kaeyris\docs\journal.md`
- [X] T048 [P] Add ADR for web user auth/session strategy in `D:\Projets\dev\AI-Kaeyris\docs\adr\0011-user-password-auth.md`
- [X] T049 [P] Update quick auth commands and rationale in `D:\Projets\dev\AI-Kaeyris\docs\playbook.md`
- [X] T050 Run migration smoke test: `alembic upgrade head`, `alembic downgrade -1`, `alembic upgrade head`
- [ ] T051 Run quickstart validation from `D:\Projets\dev\AI-Kaeyris\specs\003-user-password-auth\quickstart.md`
- [X] T052 Run full quality gate: `ruff check .`
- [X] T053 Run full test suite: `pytest`
- [X] T054 Review generated OpenAPI for login/logout/users routes via `/docs`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: no dependencies.
- **Phase 2 Foundational**: depends on Phase 1; blocks all user stories.
- **US1 MVP**: depends on Phase 2.
- **US2**: depends on US1 because full user management extends the MVP profile creation path.
- **US3**: setup behavior is part of the install story; tests can be refined after US1 because setup creates the first GM used by the MVP.
- **US4**: depends on US1 because logout and expiry operate on sessions created by login.
- **Polish**: depends on desired user stories being complete.

### User Story Dependencies

- **US1 (P1)**: Required MVP: create profile, login, cookie-auth protected call.
- **US2 (P2)**: Builds on US1 auth identity and profile creation route.
- **US3 (P3)**: Can be implemented with US1 because first-run setup creates the first GM used by the MVP.
- **US4 (P4)**: Can be implemented after US1.

### Within Each User Story

- Write tests first and verify they fail.
- Implement models/helpers before endpoints.
- Implement endpoint behavior before documentation.
- Run focused tests at each checkpoint before moving on.

---

## Parallel Opportunities

- T002 and T003 can run in parallel after T001 starts.
- T007 can run in parallel with T004-T006 because it targets schema serialization, not migrations.
- T009 and T010 can be written in parallel before T011.
- US1 tests T012-T014 can be written in parallel.
- US2 tests T022-T026 can be written in parallel.
- US3 tests T033-T034 can be written in parallel.
- US4 tests T040-T041 can be written in parallel.
- Polish docs T046-T049 can be updated in parallel after implementation stabilizes.

## Parallel Example: User Story 2

```text
Task: "T022 Add user listing tests in tests/services/jdr/test_user_management.py"
Task: "T023 Add user update tests in tests/services/jdr/test_user_management.py"
Task: "T024 Add logical delete tests in tests/services/jdr/test_user_management.py"
Task: "T025 Add authorization tests in tests/services/jdr/test_user_management.py"
Task: "T026 Add last-active-GM guard tests in tests/services/jdr/test_user_management.py"
```

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1 setup.
2. Complete Phase 2 foundational model/helpers.
3. Complete Phase 3 US1 profile creation + login.
4. Stop and validate: profile creation works, login returns cookie, exact error bodies match, cookie authenticates protected route.

### Incremental Delivery

1. US1: first-run setup creates a GM; GM can create a profile; front can login; cookie can call protected endpoints.
2. US2: GM can list, update, and logically delete users without DB edits.
3. US3: fresh installs can create first GM safely from the front.
4. US4: sessions can expire and logout.
5. Polish: docs, ADR, quickstart, full tests.

### Guardrails

- Do not introduce OAuth/OIDC/JWT in this feature.
- Do not remove existing API-key auth.
- Do not physically delete users.
- Do not expose password hashes, token hashes, or plaintext session tokens in responses or logs.
