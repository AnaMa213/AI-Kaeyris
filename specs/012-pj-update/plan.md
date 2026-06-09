# Implementation Plan: BD-12 PJ Update

**Branch**: `codex/012-pj-update` | **Date**: 2026-06-09 | **Spec**: [`spec.md`](./spec.md)
**Input**: Feature specification from `specs/012-pj-update/spec.md`

## Summary

Add a partial update endpoint for player characters:
`PATCH /services/jdr/pjs/{pj_id}`. The endpoint lets the current GM rename a
PJ and set, change, or explicitly clear its optional `user_id` link. The design
reuses the existing `Pj` table, `PjOut` response shape, duplicate-name
behavior, and campaign ownership checks. It also aligns unknown `user_id`
handling for PJ create/update with the frontend contract: `422 invalid-user`.
No database migration and no PJ deletion are in scope.

## Technical Context

**Language/Version**: Python 3.12+  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async, Alembic,
pytest/httpx, existing JDR auth/campaign helpers  
**Storage**: Existing `jdr_pjs` table with `name`, `campaign_id`, and nullable
`user_id`; existing `core_users` validation for account links  
**Testing**: pytest, httpx ASGI transport, existing DB/cookie/API-key fixtures,
OpenAPI schema assertions  
**Target Platform**: Modular-monolith FastAPI API running locally and in Docker
Compose, later Raspberry Pi 5 LAN deployment  
**Project Type**: Backend REST API feature inside existing `app/services/jdr`  
**Performance Goals**: Single-row update with existing DB transaction; no async
job and no blocking external call  
**Constraints**: Preserve GM/campaign ownership boundaries; distinguish omitted
`user_id` from explicit `null`; keep deletion out of scope; do not introduce a
new abstraction or migration  
**Scale/Scope**: One endpoint, one request schema, targeted repository/logic
update path, tests for rename/link/unlink/authorization/error contract

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Honesty over speed**: PASS. The handoff states `user_id` already exists on
  `PjCreate`/`PjOut`, and the code confirms `Pj.user_id` is already nullable.
- **Pedagogy over output volume**: PASS. Plan uses the existing PJ flow so each
  task maps to a small code change and a visible test.
- **YAGNI**: PASS. No DELETE endpoint, migration, campaign refactor, user
  management change, or new service layer is planned.
- **Strict separation of concerns**: PASS. Changes stay in `app/services/jdr`
  and reuse `app.core.models.User` only for validation already present in
  `logic.create_pj`.
- **Test discipline**: PASS. Public endpoint gets tests for success, partial
  update semantics, ownership isolation, duplicate name, invalid user, and
  OpenAPI exposure.
- **Security by default**: PASS. Update requires existing GM auth and hides
  cross-owner PJs as not found.
- **12-Factor**: PASS. No config or secret changes.

## Project Structure

### Documentation (this feature)

```text
specs/012-pj-update/
+-- plan.md
+-- research.md
+-- data-model.md
+-- quickstart.md
+-- contracts/
|   +-- rest-api.md
+-- checklists/
|   +-- requirements.md
+-- tasks.md              # Created by /speckit-tasks, not this command
```

### Source Code (repository root)

```text
app/
+-- services/
|   +-- jdr/
|       +-- schemas.py          # Add PjUpdate request schema
|       +-- logic.py            # Add update_pj business operation
|       +-- router.py           # Add PATCH /pjs/{pj_id}
|       +-- db/
|           +-- repositories.py # Add/update focused PjRepository method
+-- core/
    +-- models.py              # Existing User lookup target only

tests/
+-- services/
|   +-- jdr/
|       +-- test_pjs.py         # Add endpoint tests for PATCH behavior

docs/
+-- context/
|   +-- api/
|       +-- openapi.json        # Regenerate public frontend contract
+-- services/
|   +-- jdr.md                  # Document editable PJ behavior
+-- memo.md                    # Add quick endpoint reminder if useful
+-- journal.md                 # Jalon entry after implementation
```

**Structure Decision**: Use the existing JDR service. PJ editing is a narrow
extension of `POST/GET /services/jdr/pjs`, so adding a new module or cross-core
abstraction would be unnecessary.

## Phase 0: Research

Research output: [`research.md`](./research.md)

Resolved unknowns:

- HTTP `PATCH` is the right public verb for partial modification of an existing
  resource.
- The request schema must preserve field presence so `user_id: null` clears the
  link while omitted `user_id` leaves it unchanged.
- The existing `(owner_gm_key_id, name)` uniqueness constraint and
  `DuplicatePjNameError` path can be reused for duplicate rename rejection.
- The existing `Pj.user_id` nullable FK to `core_users.id` means BD-12 does not
  need a migration.
- Current code raises `PjAssignmentError` for unknown `user_id`, but the router
  does not yet expose the requested `invalid-user` public category. BD-12 should
  align both POST and PATCH on `422 invalid-user` rather than copy that mismatch.
- Cross-owner behavior should stay 404 by loading the row through the existing
  owner/campaign-scoped repository pattern.

## Phase 1: Design

Design outputs:

- [`data-model.md`](./data-model.md)
- [`contracts/rest-api.md`](./contracts/rest-api.md)
- [`quickstart.md`](./quickstart.md)

Implementation shape:

1. Add `PjUpdate` with optional `name` and optional nullable `user_id`.
2. Add `logic.update_pj(...)` that loads only an owned PJ, validates any
   provided `user_id`, applies provided fields, flushes to catch duplicate
   names, commits, refreshes, and returns `Pj`.
3. Add or reuse a PJ user-assignment app error mapped to `422 invalid-user`, and
   apply it consistently to both `POST /pjs` and `PATCH /pjs/{pj_id}` when a
   non-null `user_id` is unknown.
4. Add `PATCH /services/jdr/pjs/{pj_id}` mapping domain errors to public
   categories: `404 pj-not-found`, `409 duplicate-pj`, and `422 invalid-user`.
5. Add tests in `tests/services/jdr/test_pjs.py` for rename, link, unlink,
   both-fields update, no-op payload, duplicate name, unknown user, cross-owner
   404, and OpenAPI exposure.
6. Regenerate `docs/context/api/openapi.json` and update JDR docs/memo/journal
   during implementation.

## Phase 1 Constitution Re-check

- **YAGNI**: PASS. The design still adds one endpoint and no deletion/migration.
- **Separation of concerns**: PASS. Business logic stays in JDR logic/repository;
  router remains HTTP translation only.
- **Test discipline**: PASS. Endpoint behavior and frontend contract are both
  explicitly testable.
- **Security/12-Factor**: PASS. Auth and ownership checks reuse existing
  dependencies; no configuration or secrets are introduced.

## Complexity Tracking

No constitution violations.
