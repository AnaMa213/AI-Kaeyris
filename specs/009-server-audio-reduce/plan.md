# Implementation Plan: Server Audio Reduce

**Branch**: `codex/009-server-audio-reduce` | **Date**: 2026-06-03 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/009-server-audio-reduce/spec.md`

## Summary

Move JDR session audio preparation from the browser into the backend worker pipeline while preserving the existing frontend upload and polling contract. The implementation stays in the existing JDR service: enforce a documented raw upload size limit, persist accepted raw uploads as transient input, prepare a durable transcription-ready audio artifact in the existing transcription job, update the existing `AudioSource` row to point at the retained prepared file, delete raw audio after successful preparation, and keep BD-8 playback/delete semantics intact.

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2.x async ORM, Redis/RQ, structlog, ffmpeg/ffprobe runtime binaries
**Storage**: SQLite for dev/tests; PostgreSQL target; local files under `KAEYRIS_DATA_DIR`
**Testing**: pytest + httpx; ruff for linting
**Target Platform**: Linux API/worker deployable on Raspberry Pi 5; local development on Windows supported
**Project Type**: Backend web service in a modular monolith
**Performance Goals**: Raw uploads up to the configured limit stream to disk without loading the full file into memory; accepted uploads return `202` before audio preparation; prepared audio is small enough for downstream transcription constraints; one current job remains pollable throughout preparation and transcription
**Constraints**: Keep changes inside JDR service/job boundaries plus environment config; no new public route; no new user-visible job kind; no new user-visible session state; no new storage backend; raw audio deleted after successful preparation; effective upload limit documented and surfaced on 413
**Scale/Scope**: Single-user/local-network oriented platform; one uploaded recording per session; product target up to 500 MB raw uploads; no multi-job listing or generic media service in scope

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Honesty over speed**: PASS. Current code was inspected: upload streams to `KAEYRIS_DATA_DIR/audios`, Docker already installs ffmpeg/ffprobe, transcription already chunks audio in the worker, and BD-8 exposes `current_job_id`.
- **Pedagogy over output volume**: PASS. The plan keeps one logical change and documents why the job/state/API contract stays stable.
- **YAGNI**: PASS. No `audio_reduce` job kind, no `reducing` state, no dedicated media service, no new table unless implementation discovers a concrete need and asks first.
- **Strict separation of concerns**: PASS. JDR audio preparation remains in `app/services/jdr` and `app/jobs/jdr`; business code continues to call provider-agnostic transcription adapters.
- **Test discipline**: PASS. Public upload/audio endpoints, worker preparation behavior, failure paths, and delete cleanup require focused tests before implementation.
- **Security by default**: PASS. Upload size becomes explicit to avoid unbounded resource consumption; auth/campaign scoping from existing JDR routes remains unchanged.
- **12-Factor compliance**: PASS. Upload limit and data directory remain environment-driven; logs stay stdout events; local files remain configured backing storage.

## Project Structure

### Documentation (this feature)

```text
specs/009-server-audio-reduce/
|-- plan.md
|-- research.md
|-- data-model.md
|-- quickstart.md
|-- contracts/
|   `-- rest-api.md
`-- tasks.md
```

### Source Code (repository root)

```text
app/
|-- core/
|   `-- config.py                         # upload limit env setting
|-- jobs/
|   `-- jdr.py                            # transcription job: prepare audio before adapter calls
`-- services/
    `-- jdr/
        |-- audio.py                      # server-side audio preparation helper
        |-- logic.py                      # upload limit, transient raw storage, purge orchestration
        |-- batch/
        |   `-- router.py                 # 413 mapping and unchanged upload contract
        `-- db/
            `-- repositories.py           # update AudioSource canonical file metadata

tests/
|-- jobs/
|   `-- test_transcribe_audio_reduce.py    # worker preparation success/failure
`-- services/
    `-- jdr/
        |-- test_audio_upload.py          # size limit + stable response/job contract
        |-- test_audio_get.py             # serves prepared retained audio
        `-- test_audio_purge.py           # raw leftovers + prepared audio cleanup

docs/
|-- journal.md
|-- memo.md
`-- runbook.md                            # only if deployment limit/manual operation changes
```

**Structure Decision**: Use the existing JDR service layout. Audio preparation is part of the JDR session lifecycle and should not create a cross-service media abstraction for this milestone.

## Phase 0 Research

Completed in [research.md](research.md).

Key decisions:

- Keep one frontend-visible `transcription` job.
- Introduce a durable prepared audio artifact represented by the existing `AudioSource` row.
- Enforce a configured upload size limit, default target 500 MB.
- Run audio preparation in the worker, not during the HTTP request.
- Keep existing chunked transcription downstream from the prepared artifact.
- Treat preparation failure as failure of the existing transcription processing flow.

Sources used:

- RQ job/dependency behavior: https://python-rq.org/docs/
- OWASP API4:2023 resource consumption and file upload limits: https://owasp.org/API-Security/editions/2023/en/0xa4-unrestricted-resource-consumption/
- 12-Factor environment config: https://12factor.net/config
- FastAPI `UploadFile` behavior for large file uploads: https://fastapi.tiangolo.com/tutorial/request-files/

## Phase 1 Design

Completed artifacts:

- [data-model.md](data-model.md)
- [contracts/rest-api.md](contracts/rest-api.md)
- [quickstart.md](quickstart.md)

Design notes:

- `Session.state` remains the lifecycle source of truth; no `reducing` state.
- `Session.current_job_id` continues to point to the single visible transcription job.
- `AudioSource` remains the canonical session audio row; after successful preparation it points to the retained prepared file.
- Raw upload files are transient and removed after successful preparation.
- `GET /audio` serves the retained prepared audio after preparation.
- `DELETE /audio` removes prepared audio, raw leftovers, transcription rows, chunks, artifacts, and the job pointer.
- No database migration is expected unless implementation discovers a concrete need to store raw and prepared metadata simultaneously.

## Post-Design Constitution Check

- **YAGNI**: PASS. The design avoids a new job kind, state, table, route, and generic media service.
- **Separation of concerns**: PASS. Only JDR service/job/config surfaces are involved; no vendor name leaks into business logic.
- **Security**: PASS. Upload size is explicit and testable; existing authentication and campaign scoping remain the access boundary.
- **Tests**: PASS. Plan identifies endpoint, worker, file lifecycle, and failure-path tests.
- **12-Factor**: PASS. Operational differences are represented by environment config and documentation.

## Complexity Tracking

No constitution violations identified.
