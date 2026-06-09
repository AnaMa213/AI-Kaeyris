# Implementation Plan: Live Job Events

**Branch**: `codex/014-sse-artifact-jobs` | **Date**: 2026-06-09 | **Spec**: [`spec.md`](spec.md)
**Input**: Feature specification from `specs/014-sse-artifact-jobs/spec.md`

## Summary

BD-14 delivers the live job event channel left optional in BD-10 and applies it to every existing JDR job type: transcription, summary, narrative, elements, and POV generation. The implementation will add `GET /services/jdr/jobs/{job_id}/events` as an authenticated `text/event-stream` endpoint that repeatedly projects the same job state as `GET /services/jdr/jobs/{job_id}`, emits `event: progress` frames, sends one terminal frame on `succeeded` or `failed`, then closes.

The technical approach is intentionally narrow: reuse the current RQ-backed `JobOut` projection, keep `status` as the completion source of truth, keep artifact progress fields nullable, and avoid new persisted state or a separate event bus. The existing polling endpoint remains unchanged as the frontend fallback.

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: FastAPI, Starlette response primitives, Redis, RQ, SQLAlchemy, Pydantic v2
**Storage**: Existing Redis/RQ job state plus existing SQL job/session projections; no migration
**Testing**: pytest, pytest-asyncio, httpx ASGITransport, fakeredis
**Target Platform**: Linux web service deployable through Docker/Docker Compose on the current local-network platform
**Project Type**: Backend REST API / modular monolith service
**Performance Goals**: Terminal job updates visible to tested clients within 2 seconds; no extra writes per poll tick
**Constraints**: Preserve polling fallback; preserve GM/job isolation; stream must close on terminal status; no WebSocket or pub/sub scope expansion
**Scale/Scope**: One live stream per actively tracked frontend job; low/medium priority comfort feature for JDR artifact and transcription jobs

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| Honesty over speed | PASS | Existing job projection, tests, and docs were inspected; external streaming decisions cite official docs in `research.md`. |
| Pedagogy over output volume | PASS | Plan keeps the behavior small and explains why SSE wraps the current polling projection. |
| YAGNI | PASS | No database migration, WebSocket, Redis pub/sub, progress history, or new job state is planned. |
| Strict separation of concerns | PASS | JDR routing owns the public transport; existing job/session repositories and RQ projection remain the source of job state. |
| Test discipline | PASS | Plan adds route tests for streaming success, failure, transcription metadata, authorization, and polling fallback. |
| Security by default | PASS | Live events reuse the same GM-only visibility rules as `GET /jobs/{id}` and keep 404 hiding for foreign jobs. |
| 12-Factor compliance | PASS | No secrets or local config changes; stream reads backing services and logs/errors through existing app paths. |

## Project Structure

### Documentation (this feature)

```text
specs/014-sse-artifact-jobs/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── rest-api.md
├── checklists/
│   └── requirements.md
└── tasks.md              # Created by /speckit-tasks, not this phase
```

### Source Code (repository root)

```text
app/
├── services/
│   └── jdr/
│       ├── router.py          # Add GET /jobs/{job_id}/events and shared job projection helper
│       └── schemas.py         # Reuse JobOut; no new persisted schema required
└── jobs/
    └── jdr.py                 # No new job behavior planned; existing metadata remains source

tests/
└── services/
    └── jdr/
        └── test_jobs_route.py # Add SSE route tests and polling regression checks

docs/
├── context/api/openapi.json   # Regenerate with text/event-stream contract
├── services/jdr.md            # Document live job events and fallback
├── memo.md                    # Add command/reference row
└── journal.md                 # Add learning entry after implementation
```

**Structure Decision**: BD-14 stays inside the existing JDR service boundary. It extends the public job route surface in `app/services/jdr/router.py` and reuses `JobOut` rather than introducing a new service, adapter, queue abstraction, or database table.

## Phase 0: Research

See [`research.md`](research.md).

Resolved decisions:

1. Use Server-Sent Events over the existing HTTP API instead of WebSocket.
2. Implement streaming with the framework's existing streaming response primitives.
3. Reuse the current `JobOut` projection for each SSE event payload.
4. Poll Redis/RQ from the API process at a short interval instead of adding Redis pub/sub.
5. Close the stream after the terminal `succeeded` or `failed` event.
6. Document the endpoint explicitly in OpenAPI with `text/event-stream`.

## Phase 1: Design

Design outputs:

- [`data-model.md`](data-model.md)
- [`contracts/rest-api.md`](contracts/rest-api.md)
- [`quickstart.md`](quickstart.md)

Implementation shape:

1. Extract the existing `GET /jobs/{job_id}` projection logic into a private helper that returns `JobOut` or raises the same public errors.
2. Keep `GET /jobs/{job_id}` as a thin wrapper around that helper.
3. Add an async SSE event generator that calls the helper, serializes selected `JobOut` fields into `event: progress` frames, sleeps for about one second between non-terminal frames, and stops after terminal status.
4. Include `failure_reason` in terminal failed payloads when available.
5. Add `GET /jobs/{job_id}/events` with GM auth, Redis, DB dependencies, `text/event-stream`, and OpenAPI response documentation.
6. Add tests for artifact running-to-succeeded, artifact failed with reason, transcription metadata, already-terminal subscription, foreign/unknown jobs, and existing polling unchanged.
7. Regenerate `docs/context/api/openapi.json` and update JDR docs/memo/journal.

## Constitution Check (Post-Design)

| Principle | Status | Notes |
|-----------|--------|-------|
| Honesty over speed | PASS | Research decisions cite official WHATWG, Starlette/FastAPI, and RQ documentation. |
| Pedagogy over output volume | PASS | Contracts and quickstart explain the fallback and event format. |
| YAGNI | PASS | No new storage, no WebSocket, no pub/sub, no artifact phase synthesis. |
| Strict separation of concerns | PASS | Streaming transport remains in the JDR API layer; job execution remains in `app/jobs/`. |
| Test discipline | PASS | New behavior is independently testable through route tests without a real Redis server. |
| Security by default | PASS | The stream shares the same visibility helper as polling, preventing drift. |
| 12-Factor compliance | PASS | Stateless API process; job state remains in Redis/RQ and existing DB projections. |

## Complexity Tracking

No constitution violations.
