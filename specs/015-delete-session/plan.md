# Implementation Plan: Delete JDR Session

**Branch**: `codex/015-delete-session` | **Date**: 2026-06-09 | **Spec**: [`spec.md`](spec.md)
**Input**: Feature specification from `specs/015-delete-session/spec.md`

## Summary

Add persistent deletion for a JDR session owned by the current GM. The backend will expose a GM-only delete operation, refuse deletion when session work is still active, remove stored audio files best-effort, and rely on existing session-owned relationships for database cascade cleanup. The existing session create/list/read/update behavior remains the fallback contract for non-deleted sessions.

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: FastAPI, SQLAlchemy 2.x async ORM, Pydantic v2, Redis/RQ job projection
**Storage**: Existing SQL tables for sessions/dependencies plus filesystem audio under `KAEYRIS_DATA_DIR`; no new persisted state planned
**Testing**: pytest, pytest-asyncio, httpx ASGITransport, SQLite test database, fakeredis where job state is needed
**Target Platform**: Linux-compatible backend service, Docker/Docker Compose local stack
**Project Type**: Backend REST API inside the existing modular monolith
**Performance Goals**: Delete completes in one request for tested aggregate sizes; campaign/session reads reflect deletion immediately after commit
**Constraints**: Preserve GM isolation; no frontend mock state; no silent active-job cancellation; purge files best-effort while SQL remains source of truth
**Scale/Scope**: Single-session aggregate deletion for the existing JDR service; no bulk delete, restore, audit trail, or job cancellation protocol

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Honesty over speed**: Active job cancellation is not assumed. The plan chooses an explicit conflict because no reliable cancellation contract exists yet.
- **YAGNI**: No soft-delete, trash, bulk delete, audit log, new job scheduler behavior, or new persistence model.
- **Strict separation of concerns**: HTTP status mapping stays in `app/services/jdr/router.py`; business deletion and filesystem cleanup stay in `app/services/jdr/logic.py`; SQL operations stay in repositories/models.
- **Test discipline**: Public endpoint receives route tests; cascade and active-work behavior are covered before implementation is considered done.
- **Security by default**: Existing GM auth and campaign scope rules are reused; foreign sessions return not found.
- **12-Factor compliance**: Audio path resolution stays driven by existing config (`KAEYRIS_DATA_DIR`); no secrets or environment-specific paths are hardcoded.

Initial gate status: PASS.

## Project Structure

### Documentation (this feature)

```text
specs/015-delete-session/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── rest-api.md
└── tasks.md
```

### Source Code (repository root)

```text
app/
├── services/
│   └── jdr/
│       ├── router.py
│       ├── logic.py
│       └── db/
│           ├── models.py
│           └── repositories.py
└── core/
    └── config.py

tests/
└── services/
    └── jdr/
        └── test_sessions_delete.py

docs/
├── context/api/openapi.json
├── services/jdr.md
├── memo.md
└── journal.md
```

**Structure Decision**: Keep the change inside the existing JDR service. Reuse the existing service/router/repository layering and add one focused route test module for session deletion.

## Complexity Tracking

No constitution violations identified.

## Phase 0: Research

See [`research.md`](research.md).

## Phase 1: Design & Contracts

See [`data-model.md`](data-model.md), [`contracts/rest-api.md`](contracts/rest-api.md), and [`quickstart.md`](quickstart.md).

## Constitution Check - Post Design

- No new framework, ORM, queue, storage provider, or service split introduced.
- Active job behavior is deterministic and documented as conflict.
- Cascading remains scoped to the session aggregate and existing owned dependencies.
- Tests, OpenAPI, service docs, memo, and journal updates are planned.

Post-design gate status: PASS.
