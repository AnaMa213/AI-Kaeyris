# Implementation Plan: BD-11 LLM Connectivity

**Branch**: `codex/011-llm-connectivity` | **Date**: 2026-06-09 | **Spec**: [`spec.md`](./spec.md)
**Input**: Feature specification from `specs/011-llm-connectivity/spec.md`

## Summary

Fix the JDR non-diarised summary flow so a worker can reach the configured
LLM provider in Docker Compose and so an unavailable LLM ends as a visible
failed job with a non-empty `failure_reason`. The implementation stays inside
the existing OpenAI-compatible `LLMAdapter`, RQ job, and JDR polling contract:
no new frontend endpoint, no new provider SDK, and no database migration unless
implementation exposes a concrete persistence gap.

## Technical Context

**Language/Version**: Python 3.12+  
**Primary Dependencies**: FastAPI, Pydantic v2, Redis/RQ, SQLAlchemy async,
httpx/OpenAI-compatible SDK, structlog/prometheus-client  
**Storage**: Existing SQL tables for JDR sessions/chunks/artifacts/jobs, Redis
for RQ job runtime state  
**Testing**: pytest, httpx ASGI transport, existing fake Redis/database fixtures,
mock or intentionally unreachable LLM adapter configuration  
**Target Platform**: Docker Compose development stack, then Raspberry Pi 5 LAN
deployment path  
**Project Type**: Modular-monolith web API with background worker  
**Performance Goals**: Keep the existing async job model; LLM calls may take
seconds/minutes, but API enqueue/polling endpoints remain quick and
non-blocking  
**Constraints**: Secrets only via environment variables; `app/services/` must
depend on the adapter interface, not provider-specific classes; no frontend
contract change; no transcription regression; no `.env` commit  
**Scale/Scope**: One configured LLM provider per environment, one default RQ
queue, BD-11 limited to the non-diarised summary artifact path and shared LLM
adapter error handling

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Honesty over speed**: PASS. The observed stack trace is
  `APIConnectionError` -> `TransientLLMError` -> `TransientJobError`; the exact
  infrastructure root cause is not assumed before implementation checks.
- **Pedagogy over output volume**: PASS. Plan keeps decisions explicit and
  small enough to map to focused tasks/tests.
- **YAGNI**: PASS. No new provider, scheduler, endpoint, retry system, or
  database schema is planned.
- **Strict separation of concerns**: PASS. Provider-specific connectivity
  remains in `app/adapters/llm.py` and configuration in `app/core/config.py`;
  JDR business code continues to call the adapter boundary.
- **Test discipline**: PASS. Add targeted tests for summary success,
  unreachable LLM failure projection, and no transcription regression.
- **Security by default**: PASS. API keys remain environment variables; docs
  must not include real secrets.
- **12-Factor**: PASS. Configuration remains environment-driven and shared by
  API/worker containers.

## Project Structure

### Documentation (this feature)

```text
specs/011-llm-connectivity/
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
+-- adapters/
|   +-- llm.py             # OpenAI-compatible adapter config/error handling
+-- core/
|   +-- config.py          # LLM env settings and validation, if needed
+-- jobs/
|   +-- jdr.py             # Summary job behavior and transient/permanent errors
+-- services/
    +-- jdr/
        +-- router.py      # Existing enqueue/polling contract
        +-- schemas.py     # Existing JobOut/summary response models
        +-- db/
            +-- repositories.py

tests/
+-- adapters/
+-- jobs/
+-- services/
    +-- jdr/
        +-- test_jobs_route.py
        +-- test_summary*.py

docs/
+-- journal.md
+-- memo.md                # Add operational LLM env hints only if adopted
```

**Structure Decision**: Use the existing single backend project. BD-11 does not
create a new service; it tightens the existing JDR summary job and the shared
LLM adapter/configuration boundary.

## Phase 0: Research

Research output: [`research.md`](./research.md)

Resolved unknowns:

- The current adapter already classifies `APIConnectionError`,
  `APITimeoutError`, `RateLimitError`, and server errors as transient LLM
  errors.
- The JDR summary job already maps transient LLM errors to `TransientJobError`
  so RQ can retry according to existing enqueue policy.
- The existing `GET /services/jdr/jobs/{job_id}` route can expose a failed RQ
  job with a trimmed `failure_reason` from `job.exc_info`.
- Docker Compose already passes `.env` to both `api` and `worker`; the likely
  risk area is whether `LLM_BASE_URL` is reachable from inside the worker
  container and whether tests/documentation lock that down.

## Phase 1: Design

Design outputs:

- [`data-model.md`](./data-model.md)
- [`contracts/rest-api.md`](./contracts/rest-api.md)
- [`quickstart.md`](./quickstart.md)

Implementation shape:

1. Verify API and worker use the same LLM settings source and document the
   Docker-network form of `LLM_BASE_URL`.
2. Add or adjust targeted tests so an unreachable LLM produces a failed summary
   job projection with non-empty `failure_reason`.
3. Keep the successful summary path writing the existing `summary` artifact.
4. Preserve the existing frontend contract and transcription tests.

## Phase 1 Constitution Re-check

- **YAGNI**: PASS. Design reuses current RQ, settings, and adapter types.
- **Separation of concerns**: PASS. No provider-specific references are planned
  in `app/services/jdr`.
- **Test discipline**: PASS. Design names the public route behavior and job
  behavior to cover.
- **Security/12-Factor**: PASS. The quickstart documents env names and uses
  placeholders only.

## Complexity Tracking

No constitution violations.
