# Implementation Plan: Current Job and Audio Stream

**Branch**: `codex/008-current-job-audio-stream` | **Date**: 2026-06-03 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/008-current-job-audio-stream/spec.md`

## Summary

Expose enough server-side session state for the frontend to resume transcription polling after refresh, and make uploaded audio streamable and deliberately replaceable. The implementation stays inside the existing JDR service: add an optional current transcription job pointer to sessions, keep source audio available after terminal transcription states, add authenticated audio retrieval with byte-range support, and update destructive audio deletion semantics.

## Technical Context

**Language/Version**: Python 3.12+  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2.x async ORM, Redis/RQ, structlog  
**Storage**: SQLite for dev/tests; PostgreSQL target; local audio files under `KAEYRIS_DATA_DIR`  
**Testing**: pytest + httpx; ruff for linting  
**Target Platform**: Linux API service deployable on Raspberry Pi 5; local development on Windows supported  
**Project Type**: Backend web service in a modular monolith  
**Performance Goals**: Session detail can expose polling state without extra discovery calls; audio playback starts within 2 seconds in normal local conditions; seek requests return only requested byte ranges  
**Constraints**: Keep JDR business logic within `app/services/jdr`; no new service split; no new storage backend; no signed-URL flow for this story; keep current authentication and campaign scoping semantics  
**Scale/Scope**: Single-user/local-network oriented platform; one current transcription job pointer per session; Epic 4 multi-job listing remains out of scope

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Honesty over speed**: PASS. Existing code was inspected before planning; current behavior differs from BD-8 on audio auto-purge and DELETE semantics and is called out explicitly.
- **Pedagogy over output volume**: PASS. The plan decomposes one logical change and documents why each behavior changes.
- **YAGNI**: PASS. No multi-job dictionary, no signed URL, no generic media service, and no Epic 4 active-job listing.
- **Strict separation of concerns**: PASS. Changes remain in JDR service, JDR jobs, JDR repositories, and storage helpers.
- **Test discipline**: PASS. Public session/audio endpoints and non-trivial lifecycle transitions require tests.
- **Security by default**: PASS. Existing auth/campaign scoping remains; cross-campaign audio lookup must not reveal resource existence.
- **12-Factor compliance**: PASS. Config remains environment-driven; local files remain backing storage under configured data directory; logs remain event streams.

## Project Structure

### Documentation (this feature)

```text
specs/008-current-job-audio-stream/
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
├── jobs/
│   └── jdr.py                         # transcription lifecycle: keep audio, preserve current_job_id
└── services/
    └── jdr/
        ├── batch/
        │   └── router.py              # POST/GET/DELETE audio routes
        ├── db/
        │   ├── models.py              # Session.current_job_id relationship/column
        │   └── repositories.py        # audio/job pointer + purge helpers
        ├── logic.py                   # upload, stream lookup, destructive purge orchestration
        ├── router.py                  # SessionOut mapping/list/detail exposure
        └── schemas.py                 # SessionOut.current_job_id

migrations/
└── versions/                          # additive nullable current_job_id migration

tests/
├── jobs/
│   └── test_transcribe*.py            # terminal transcription keeps source audio and job pointer
└── services/
    └── jdr/
        ├── test_audio_get.py          # full/range audio retrieval
        ├── test_audio_purge.py        # irreversible/idempotent DELETE matrix
        ├── test_audio_upload.py       # upload sets current_job_id
        └── test_sessions.py           # SessionOut exposes current_job_id
```

**Structure Decision**: Use the existing backend service layout. No new bounded context or generic media module is introduced because audio belongs to the JDR session lifecycle for this milestone.

## Phase 0 Research

Completed in [research.md](research.md).

Key decisions:

- Store `current_job_id` on the session and expose it as nullable/additive.
- Keep the pointer after job success/failure.
- Preserve source audio after success/failure until explicit delete.
- Stream audio directly with byte-range support.
- Make audio deletion irreversible and idempotent except while actively transcribing.
- Keep scope inside JDR service boundaries.

## Phase 1 Design

Completed artifacts:

- [data-model.md](data-model.md)
- [contracts/rest-api.md](contracts/rest-api.md)
- [quickstart.md](quickstart.md)

Design notes:

- `Session.state` remains the source of truth for lifecycle state; `current_job_id` is only a pointer for polling/failure context.
- `DELETE /audio` becomes the only purge path for source audio in this story.
- Successful transcription no longer marks `AudioSource.purged_at` or deletes the source file.
- Failed transcription preserves source audio and current job pointer.
- Deletion removes all data derived from the previous audio and resets the session to `created`.

## Post-Design Constitution Check

- **YAGNI**: PASS. Epic 4 job listing remains documented but unimplemented.
- **Separation of concerns**: PASS. Contracts and design touch only JDR-specific modules plus the existing migration area.
- **Security**: PASS. Audio retrieval is scoped through the existing auth/campaign model; unauthorized and absent audio both return not-found behavior.
- **Tests**: PASS. Plan includes endpoint, lifecycle, stream/range, and job-flow tests before implementation.
- **12-Factor**: PASS. No hardcoded storage path or secret; all file storage remains under existing config.

## Complexity Tracking

No constitution violations identified.
