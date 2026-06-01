# Implementation Plan: Timezone-Aware Datetime Serialization

**Branch**: `main` | **Date**: 2026-06-01 | **Spec**: [`spec.md`](./spec.md)  
**Input**: Feature specification from `specs/005-tz-aware-datetime-serialization/spec.md`

## Summary

Fix the backend response contract so every public non-empty datetime is serialized with an explicit timezone suffix. The plan keeps the change small and test-driven: add a shared UTC-aware datetime serialization helper, wire it through the existing Pydantic response schemas that expose datetimes, and cover representative JDR/session/user/auth responses with regression tests.

The storage model already declares timezone-aware columns in the current codebase, so the first implementation step should prove the serialization failure at the response boundary before considering a migration. This avoids a broad database change for a contract bug that can be corrected at the schema boundary.

## Technical Context

**Language/Version**: Python 3.12+  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2.x async, Alembic already present  
**Storage**: SQLite via `aiosqlite` in dev, PostgreSQL via `asyncpg` in target deployment; existing datetime columns already use timezone-aware declarations  
**Testing**: pytest + pytest-asyncio + httpx ASGITransport; targeted response contract tests plus existing route tests  
**Target Platform**: Local development on Windows/Linux; Raspberry Pi 5 deployment later behind Caddy  
**Project Type**: Web-service API in a modular monolith  
**Performance Goals**: Datetime serialization adds no user-visible latency to normal interactive API responses  
**Constraints**: No request-body contract break; no new dependency; no frontend change; no endpoint-by-endpoint manual formatting in route handlers  
**Scale/Scope**: Personal sandbox scale; cover existing public JDR and auth responses that expose datetime fields

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Justification |
|---|---|---|
| Honesty over speed | PASS | The observed bug comes from a frontend handoff; the plan starts with failing backend response tests and does not assume storage is the only cause. |
| Pedagogy over output volume | PASS | Research documents why the response-schema boundary is chosen and what alternatives were rejected. |
| YAGNI | PASS | No new date library, no timezone feature, no frontend cleanup, and no migration unless tests prove storage changes are required. |
| Strict separation of concerns | PASS | Business logic remains unchanged; serialization behavior belongs to schema/contract code, not JDR route workflows. |
| Test discipline | PASS | Public response payloads that expose datetime fields get regression coverage before implementation. |
| Security by default | PASS | The change does not weaken auth, expose secrets, or alter authorization scope. |
| 12-Factor | PASS | No new config, secrets, backing services, or environment-specific behavior. |
| Locked stack | PASS | Uses the existing Python/FastAPI/Pydantic/pytest stack only. |

**Verdict**: PASS. No constitution violation requires Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/005-tz-aware-datetime-serialization/
|-- spec.md
|-- plan.md
|-- research.md
|-- data-model.md
|-- quickstart.md
|-- contracts/
|   `-- rest-api.md
|-- checklists/
|   `-- requirements.md
`-- tasks.md              # generated later by /speckit-tasks
```

### Source Code (repository root)

```text
app/
|-- core/
|   |-- datetime_serialization.py  # shared UTC-aware datetime JSON helper
|   `-- user_schemas.py            # apply helper to auth/user datetime outputs
`-- services/
    `-- jdr/
        `-- schemas.py             # apply helper to JDR datetime outputs

tests/
|-- core/
|   `-- test_datetime_serialization.py
`-- services/
    `-- jdr/
        |-- test_datetime_serialization.py
        |-- test_sessions.py       # extend existing response assertions if clearer
        |-- test_pjs.py            # extend existing response assertions if clearer
        `-- test_auth_me.py        # extend once BD-4 auth context is stable
```

**Structure Decision**: Keep the reusable serializer in `app/core` because datetime JSON encoding is a cross-cutting API contract. Keep endpoint-specific assertions near existing JDR route tests so failures point to the public behavior that regressed.

## Complexity Tracking

> Empty. No constitution violation.

## Phase 0: Research Summary

See [`research.md`](./research.md). Decisions resolved before design:

- Normalize response datetimes to aware UTC at serialization time.
- Implement with Pydantic v2 field/model serialization on shared base/helper code, not manual route formatting.
- Keep accepting timezone-naive inputs but interpret them as UTC at the feature boundary.
- Defer database migration unless failing tests prove persisted values cannot be normalized safely.

## Phase 1: Design Summary

See [`data-model.md`](./data-model.md), [`contracts/rest-api.md`](./contracts/rest-api.md), and [`quickstart.md`](./quickstart.md).

### Post-Design Constitution Re-check

| Principle | Status | Justification |
|---|---|---|
| Honesty over speed | PASS | Research cites official Python, Pydantic, and FastAPI docs and separates verified behavior from implementation choice. |
| Pedagogy over output volume | PASS | The data model and contract docs explain the boundary decision without introducing large code volume. |
| YAGNI | PASS | The design adds one small helper and schema wiring only; no new dependency or timezone preference feature. |
| Strict separation of concerns | PASS | Routes continue returning domain objects; schemas own JSON shape. |
| Test discipline | PASS | Quickstart and contract docs identify exact regression checks before implementation. |
| Security by default | PASS | Existing authentication and authorization remain untouched. |
| 12-Factor | PASS | No environment-specific config or new backing service. |
| Locked stack | PASS | Existing locked stack only. |

**Verdict**: PASS. Ready for `/speckit-tasks`.
