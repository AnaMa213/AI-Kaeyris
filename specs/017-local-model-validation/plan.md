# Implementation Plan: Local Model Validation

**Branch**: `main` | **Date**: 2026-06-16 | **Spec**: [`spec.md`](spec.md)
**Input**: Feature specification from `specs/017-local-model-validation/spec.md`

## Summary

Add a protected JDR local-model validation operation, store short-lived validation proofs, require matching proofs when Local paths are changed through model settings, and route jobs to Local in-process adapters when validated Local settings are saved. The proof value returned to clients is opaque; only its SHA-256 hash is stored server-side.

## Technical Context

**Language/Version**: Python 3.12+  
**Primary Dependencies**: FastAPI, SQLAlchemy 2.x async ORM, Alembic, Pydantic v2, RQ, existing OpenAI-compatible adapters, optional local runtime packages (`faster-whisper`, `llama-cpp-python`) loaded lazily  
**Storage**: Additive SQL changes: one `jdr_local_model_validations` table plus nullable proof-hash columns on `jdr_model_settings`; SQLite in tests/local host, PostgreSQL target/dev Compose  
**Testing**: pytest, pytest-asyncio, httpx ASGITransport, in-memory SQLite fixtures, monkeypatched local runtime probes/adapters for deterministic tests  
**Target Platform**: Linux-compatible backend service and RQ worker, Docker/Docker Compose local stack, Raspberry Pi 5 deployment path documented  
**Project Type**: Backend REST API inside the existing modular monolith  
**Performance Goals**: Validation probe bounded by configurable timeout; PATCH proof lookup is a single indexed read; jobs do not hold database sessions during model execution  
**Constraints**: No raw path errors, stack traces, runtime logs, API keys, or secrets in responses; no silent operator fallback when a saved Local setting fails at runtime; preserve existing fallback for unresolved owners/settings; optional heavy runtime dependencies must not break default test/dev install  
**Scale/Scope**: One JDR settings endpoint addition, one existing PATCH extension, local adapter routing for transcription plus narrative/elements/POV/summary jobs

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Honesty over speed**: Runtime packages are optional and lazily imported. If they are absent, validation and Local jobs fail with explicit user-safe errors instead of pretending Local mode works.
- **YAGNI**: No model catalog, auto-download, GPU scheduler, Kubernetes, queue split, encryption subsystem, or new service boundary.
- **Strict separation of concerns**: Runtime/library-specific code lives in adapters; JDR service code validates settings, stores proofs, and enforces business rules. Business routing continues to use adapter factories.
- **Test discipline**: Public validation endpoint, PATCH proof enforcement, proof binding/expiry, and job Local routing all get tests before implementation tasks.
- **Security by default**: Proofs are bound to user/category/path hash/status/expiry and stored as hashes. Error bodies use the existing RFC 9457 Problem Details handler and avoid raw runtime details; this aligns with RFC 9457 (https://www.rfc-editor.org/rfc/rfc9457.html) and OWASP API Security guidance on limiting exposed object details (https://owasp.org/API-Security/editions/2023/en/0xa3-broken-object-property-level-authorization/).
- **12-Factor compliance**: Timeouts, local runtime knobs, and deployment paths are environment configuration, consistent with 12-Factor config guidance (https://12factor.net/config). Model files remain backing resources/mounted files, not committed code.

Initial gate status: PASS.

## Project Structure

### Documentation (this feature)

```text
specs/017-local-model-validation/
|-- spec.md
|-- plan.md
|-- research.md
|-- data-model.md
|-- quickstart.md
|-- contracts/
|   `-- rest-api.md
|-- checklists/
|   `-- requirements.md
`-- tasks.md
```

### Source Code (repository root)

```text
app/
|-- adapters/
|   |-- llm.py
|   |-- local_models.py
|   `-- transcription.py
|-- core/
|   `-- config.py
|-- jobs/
|   `-- jdr.py
`-- services/
    `-- jdr/
        |-- auth_router.py
        |-- local_model_validation.py
        |-- schemas.py
        `-- db/
            |-- models.py
            `-- repositories.py

migrations/
`-- versions/
    `-- 0017_jdr_local_model_validation.py

tests/
|-- adapters/
|   `-- test_local_models.py
`-- services/
    `-- jdr/
        |-- test_local_model_validation.py
        |-- test_model_settings.py
        `-- test_pipeline_model_routing.py

docs/
|-- context/api/openapi.json
|-- services/jdr.md
|-- memo.md
`-- journal.md
```

**Structure Decision**: Keep BD-20 in the existing JDR settings and job-routing surface. Add a small adapter module for local runtime probes so vendor/runtime imports never enter `app/services/jdr/`. Use additive database changes only.

## Complexity Tracking

No constitution violations identified.

## Phase 0: Research

See [`research.md`](research.md).

## Phase 1: Design & Contracts

See [`data-model.md`](data-model.md), [`contracts/rest-api.md`](contracts/rest-api.md), and [`quickstart.md`](quickstart.md).

## Constitution Check - Post Design

- No locked stack replacement, service split, ORM replacement, or queue redesign.
- Runtime dependencies are optional and lazy; default install remains lightweight.
- Proof records are short-lived, user-bound, category-bound, path-bound, and stored by hash.
- Local runtime failures for saved Local settings become explicit job failures, while unresolved owner/settings preserve BD-19 fallback.
- Tests, OpenAPI regeneration, service docs, memo, and journal updates are planned.

Post-design gate status: PASS.
