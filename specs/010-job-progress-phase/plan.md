# Implementation Plan: JDR Job Progress Phase

**Branch**: `codex/010-job-progress-phase` | **Date**: 2026-06-03 | **Spec**: [`spec.md`](spec.md)
**Input**: Feature specification from `specs/010-job-progress-phase/spec.md`

## Summary

BD-10 adds real progress information to the existing JDR transcription job polling contract. The backend will expose two nullable fields on `JobOut`: a closed `phase` vocabulary (`reducing`, `transcribing`, `done`, `failed`) and a real `progress_percent` integer from 0 to 100.

The implementation approach is deliberately narrow: keep `GET /services/jdr/jobs/{job_id}` as the frontend polling surface, store best-effort progress on the existing RQ job metadata, instrument only the transcription worker path, and avoid a new SSE/WebSocket channel for v1. RQ documents `job.meta`, `get_current_job()`, `job.save_meta()`, and refreshed reads via `get_meta(refresh=True)` for custom job data, which matches the process boundary between worker and web without adding a new storage table or stream. Source: https://python-rq.org/docs/jobs/

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2.x async, Redis/RQ, pytest, httpx, fakeredis
**Storage**: Redis/RQ job metadata for progress; existing SQL session state remains unchanged; no database migration
**Testing**: pytest + pytest-asyncio + httpx ASGITransport; fakeredis for job-route tests; ruff for lint
**Target Platform**: Local/LAN FastAPI service with RQ worker, deployable through Docker Compose
**Project Type**: Modular monolith web service
**Performance Goals**: Keep polling reads lightweight for 1-2 second frontend refresh intervals; avoid database writes per audio chunk
**Constraints**: No new streaming endpoint in v1; no new persistence table/column; all public output validated by Pydantic; no vendor-specific code in `app/services/jdr/`; job completion remains driven by existing job/session status
**Scale/Scope**: Personal JDR transcription workflow; one transcription job per uploaded session; progress is best-effort and temporary

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| Honesty over speed | PASS | Verified current code: `JobOut` lacks progress fields, `get_job` projects RQ jobs, and `_transcribe_with_optional_chunking` has no progress callback yet. |
| Pedagogy over output volume | PASS | Plan documents why `job.meta` is selected and why SSE is deferred. |
| YAGNI | PASS | Scope is limited to enriched polling; no SSE, WebSocket, database migration, or historical progress tracking. |
| Strict separation of concerns | PASS | Worker progress stays in `app/jobs/jdr.py`; public JDR contract stays in `app/services/jdr/schemas.py` and `router.py`; transcription helper remains queue-agnostic through a callback. |
| Test discipline | PASS | Public endpoint contract gets route tests; chunk progress callback gets unit tests; OpenAPI artifact must be regenerated and checked. |
| Security by default | PASS | Existing auth and cross-campaign job isolation are unchanged; absent progress metadata returns nullable fields, not internal Redis errors. |
| 12-Factor compliance | PASS | Runtime config remains environment-driven and progress is transient backing-service state, not local process state. |
| Locked stack | PASS | Uses existing Python/FastAPI/Pydantic/RQ/Redis stack; no forbidden stack change. |

## Project Structure

### Documentation (this feature)

```text
specs/010-job-progress-phase/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── rest-api.md
├── checklists/
│   └── requirements.md
└── spec.md
```

### Source Code (repository root)

```text
app/
├── jobs/
│   └── jdr.py                 # emit transcription progress and keep chunk helper testable
└── services/jdr/
    ├── router.py              # enrich GET /jobs/{job_id} from RQ metadata
    └── schemas.py             # add nullable phase/progress_percent to JobOut

tests/
├── jobs/
│   └── test_jdr_summary.py    # add focused callback/progress helper coverage near job-core tests
└── services/jdr/
    └── test_jobs_route.py     # add JobOut metadata, fallback, and failed-progress route cases

docs/
├── context/api/openapi.json   # regenerate public contract after schema change
├── journal.md                 # add learning entry at implementation completion
├── memo.md                    # add quick reference for job progress fields
└── services/jdr.md            # document the enriched job status contract if currently covered there
```

**Structure Decision**: Keep the existing modular monolith layout. This feature touches an existing JDR endpoint and the existing transcription worker only; creating a new service, table, router, or streaming module would widen the scope without current user value.

## Complexity Tracking

No constitution violations require extra complexity tracking.

## Phase 0: Research Summary

See [`research.md`](research.md). Key decisions:

- Use RQ `job.meta` as the worker-to-web progress channel for v1.
- Keep `phase` nullable and exclude `queued` from the phase enum.
- Add a queue-agnostic progress callback to `_transcribe_with_optional_chunking`.
- Keep `100` reserved for the persisted successful terminal state.
- Defer SSE/WebSocket until a measured UX need appears.

## Phase 1: Design Summary

See [`data-model.md`](data-model.md), [`contracts/rest-api.md`](contracts/rest-api.md), and [`quickstart.md`](quickstart.md).

Design highlights:

- `JobOut.phase` is `reducing | transcribing | done | failed | null`.
- `JobOut.progress_percent` is an integer `0..100` or `null`.
- Progress metadata is best-effort: missing/expired metadata never turns a valid job into a server error.
- The transcription chunk loop reports `(chunks_done, chunks_total)` through a callback, keeping Redis/RQ out of the chunking helper.
- The OpenAPI artifact must expose the two nullable fields so the frontend can regenerate typed clients from the contract. FastAPI includes `response_model` schemas in OpenAPI, and Pydantic fields can carry constraints/defaults into JSON Schema metadata. Sources: https://fastapi.tiangolo.com/advanced/additional-responses/ and https://docs.pydantic.dev/latest/concepts/fields/

## Post-Design Constitution Check

| Principle | Status | Notes |
|---|---|---|
| Honesty over speed | PASS | Research distinguishes verified RQ/FastAPI/Pydantic behavior from local implementation choices. |
| Pedagogy over output volume | PASS | Contracts and quickstart explain why progress is nullable and why job status remains authoritative. |
| YAGNI | PASS | No SSE phase-2 implementation is planned; no progress history is persisted. |
| Strict separation of concerns | PASS | `app/services/jdr/` consumes progress as public contract only; RQ-specific write logic stays in the job boundary. |
| Test discipline | PASS | Route, worker, callback, failure, fallback, and OpenAPI checks are listed before implementation. |
| Security by default | PASS | Existing authentication and tenant isolation remain in force for job polling. |
| 12-Factor compliance | PASS | Redis remains the backing service for async job state; no config/secrets change. |
| Locked stack | PASS | No stack change introduced. |
