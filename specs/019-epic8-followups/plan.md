# Implementation Plan: Epic 8 Follow-ups

**Branch**: `codex/028-epic8-followups` | **Date**: 2026-06-30 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `specs/019-epic8-followups/spec.md`

## Summary

Issue #28 asks for post-review hardening of Epic 8 artifact editing and player reads. The implementation stays inside the existing JDR modular-monolith service: add route-level guards for artifact edits, tighten validation for destructive/oversized edit payloads, align artifact provenance defaults, and make player-read participation mode-aware for `non_diarised` sessions.

## Technical Context

**Language/Version**: Python 3.12+  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async, Redis/RQ  
**Storage**: Existing `jdr_*` SQL tables on SQLite dev / PostgreSQL target; no schema migration planned  
**Testing**: pytest + httpx route tests, focused repository/model tests, ruff  
**Target Platform**: Local-network REST API, Dockerized for Raspberry Pi deployment  
**Project Type**: Modular monolith web service  
**Performance Goals**: Edit/read guards add only one lightweight job/session lookup per affected request  
**Constraints**: Keep behavior scoped to `app/services/jdr`; do not change providers, auth model, or artifact storage shape  
**Scale/Scope**: One service feature, five review follow-ups, no new service or database table

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Honesty over speed**: PASS. Issue #28 is the source; tests will report only executed results.
- **Pedagogy over output volume**: PASS. Small behavior-focused changes with focused tests.
- **YAGNI**: PASS. No new abstraction beyond small helpers needed by four edit endpoints.
- **Strict separation of concerns**: PASS. HTTP errors/guards in `router.py`, business participation helper in `logic.py`, persistence cleanup in `models.py`/`repositories.py`.
- **Test discipline**: PASS. Public endpoint changes get regression tests.
- **Security by default**: PASS. Payload validation bounds pathological input and prevents accidental destructive edits.
- **12-Factor compliance**: PASS. No config/secrets/process model change.

## Project Structure

### Documentation (this feature)

```text
specs/019-epic8-followups/
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
`-- services/
    `-- jdr/
        |-- router.py
        |-- schemas.py
        |-- logic.py
        `-- db/
            |-- models.py
            `-- repositories.py

tests/
`-- services/
    `-- jdr/
        |-- test_artifact_edit.py
        |-- test_artifact_elements_freeform.py
        |-- test_artifact_text_length.py
        |-- test_player_artifact_reads.py
        `-- test_player_listing.py
```

**Structure Decision**: Extend the existing JDR service in place. The follow-ups refine current endpoints and entity behavior; introducing new modules would add indirection without a new bounded context.

## Complexity Tracking

No constitution violations.

## Phase 0: Research

Research decisions are captured in [research.md](research.md):

1. Guard manual artifact edits with current artifact job state.
2. Confirm empty elements replacement explicitly.
3. Make player participation mode-aware.
4. Align artifact edit provenance defaults without migration.
5. Add a generous hard cap for text edits.

## Phase 1: Design

Design artifacts:

- [data-model.md](data-model.md): existing entities and validation transitions.
- [contracts/rest-api.md](contracts/rest-api.md): changed endpoint contracts.
- [quickstart.md](quickstart.md): focused validation workflow.

## Post-Design Constitution Check

- **Honesty over speed**: PASS. The plan identifies validation commands and does not assume outcomes.
- **Pedagogy over output volume**: PASS. The implementation is decomposed by user story and review finding.
- **YAGNI**: PASS. No speculative per-element CRUD, artifact versioning, or schema migration.
- **Strict separation of concerns**: PASS. All changes remain inside the JDR service boundary.
- **Test discipline**: PASS. Each public behavior change has a targeted regression test.
- **Security by default**: PASS. The change reduces accidental destructive writes and pathological payload risk.
- **12-Factor compliance**: PASS. No secrets, environment, or process model changes.
