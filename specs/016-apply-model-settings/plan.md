# Implementation Plan: Apply Model Settings to Generation Pipeline

**Branch**: `main` | **Date**: 2026-06-16 | **Spec**: [`spec.md`](spec.md)
**Input**: Feature specification from `specs/016-apply-model-settings/spec.md`

## Summary

Wire saved per-GM JDR model settings into the background transcription and LLM generation pipeline. The implementation keeps existing environment-configured adapter singletons for dependency-injection and non-user-specific callers, adds explicit factory parameters for per-job adapter construction, resolves the owning GM for each session, persists the missing Ollama summary model field, and updates settings reads so users without saved rows see the effective operator defaults without exposing secrets.

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: FastAPI, SQLAlchemy 2.x async ORM, Alembic, Pydantic v2, RQ, OpenAI-compatible SDK adapters
**Storage**: Existing SQL tables plus one nullable column on `jdr_model_settings`; SQLite in tests/local host, PostgreSQL target/dev Compose
**Testing**: pytest, pytest-asyncio, httpx ASGITransport, in-memory SQLite fixtures, focused adapter-routing tests
**Target Platform**: Linux-compatible backend service and RQ worker, Docker/Docker Compose local stack
**Project Type**: Backend REST API inside the existing modular monolith
**Performance Goals**: Per-job settings lookup completes before the long external model call; no database session is held while model calls run
**Constraints**: No raw API key exposure; no local in-process execution in BD-19; preserve existing env fallback and cached DI behavior; no new provider transport
**Scale/Scope**: Single JDR service pipeline change across existing transcription, narrative, elements, POV, and summary jobs

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Honesty over speed**: BD-19 does not claim local in-process execution; unsupported local choices fall back and log explicitly.
- **YAGNI**: No model discovery, cost estimation, key encryption, local model loader, queue redesign, or new service split.
- **Strict separation of concerns**: Provider transport knowledge stays in adapters; JDR jobs resolve user settings and choose functional route only. Existing settings endpoints remain in the JDR auth router because they are already account-level JDR settings.
- **Test discipline**: New tests cover adapter factories, per-user routing, owner resolution, settings serialization, and persistence of the Ollama model field.
- **Security by default**: Raw operator and personal keys are never serialized or logged; `.env` remains the secret boundary, aligned with 12-Factor config guidance (https://12factor.net/config) and OWASP API security concerns about broken object/property exposure (https://owasp.org/API-Security/editions/2023/en/0xa3-broken-object-property-level-authorization/).
- **12-Factor compliance**: Operator defaults continue to come from environment config; per-user settings are backing-service state and not hardcoded.

Initial gate status: PASS.

## Project Structure

### Documentation (this feature)

```text
specs/016-apply-model-settings/
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
|   `-- transcription.py
|-- jobs/
|   `-- jdr.py
|-- services/
|   `-- jdr/
|       |-- auth_router.py
|       |-- schemas.py
|       `-- db/
|           |-- models.py
|           `-- repositories.py
`-- core/
    `-- config.py

migrations/
`-- versions/
    `-- 0016_jdr_model_settings_ollama_model.py

tests/
|-- adapters/
|   |-- test_llm.py
|   `-- test_transcription.py
`-- services/
    `-- jdr/
        |-- test_model_settings.py
        `-- test_pipeline_model_routing.py

docs/
|-- context/api/openapi.json
|-- services/jdr.md
|-- memo.md
`-- journal.md
```

**Structure Decision**: Keep BD-19 inside the existing adapter/job/JDR settings seams. The only schema migration is additive. No new service, queue, ORM abstraction, or transport layer is introduced.

## Complexity Tracking

No constitution violations identified.

## Phase 0: Research

See [`research.md`](research.md).

## Phase 1: Design & Contracts

See [`data-model.md`](data-model.md), [`contracts/rest-api.md`](contracts/rest-api.md), and [`quickstart.md`](quickstart.md).

## Constitution Check - Post Design

- No framework, ORM, queue, storage provider, or service split added.
- The new `ollama_model` field is additive and nullable.
- Adapter factory compatibility is preserved through existing cached getters.
- Per-user settings lookup is done before external calls, so database sessions are not held across slow network/model work.
- Secret exposure is constrained to a boolean indicator in public settings responses.
- Tests, OpenAPI regeneration, service docs, memo, and journal updates are planned.

Post-design gate status: PASS.
