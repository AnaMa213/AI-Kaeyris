# Implementation Plan: BD-13 Transcription Edit

**Branch**: `main` | **Date**: 2026-06-09 | **Spec**: [`spec.md`](./spec.md)  
**Input**: Feature specification from `specs/013-transcription-edit/spec.md`

## Summary

Add a persisted Markdown override for transcribed JDR sessions:
`PUT /services/jdr/sessions/{session_id}/transcription` saves the GM-edited
Markdown text. `GET /services/jdr/sessions/{session_id}/transcription.md`
returns the edited text when it exists, otherwise the existing automatic
rendering. Artifact generation launched after an edit must prefer the edited
Markdown as source text, while original diarised segments and non-diarised
chunks remain unchanged.

The chosen design is one nullable text field on `jdr_sessions` because the
handoff requests a single session-level Markdown override and explicitly avoids
structured chunk/segment editing.

## Technical Context

**Language/Version**: Python 3.12+  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async, Alembic,
Redis/RQ background jobs, pytest/httpx, existing JDR auth/campaign helpers  
**Storage**: Existing SQL database with Alembic migrations; add nullable
session-level edited Markdown storage on `jdr_sessions`  
**Testing**: pytest, httpx ASGI transport, existing async DB fixtures, targeted
worker tests for summary source selection, OpenAPI schema assertions  
**Target Platform**: Modular-monolith FastAPI API running locally and in Docker
Compose, later Raspberry Pi 5 LAN deployment  
**Project Type**: Backend REST API feature inside existing `app/services/jdr`
and `app/jobs/jdr.py` generation pipeline  
**Performance Goals**: Single-session read/write stays one database lookup plus
one text field; generation source selection adds no external calls beyond the
existing LLM work  
**Constraints**: Preserve GM/campaign ownership boundaries; edit only
`state=transcribed` sessions; do not rewrite chunks/segments; keep reset/delete
out of BD-13; preserve current diarised/non-diarised read behavior except for
Markdown override fallback  
**Scale/Scope**: One write endpoint, one schema pair, one migration, focused
repository/logic changes, Markdown read behavior, summary source selection, API
contract/docs/tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Honesty over speed**: PASS. The handoff explicitly recommends the Markdown
  override approach and the current code confirms automatic data is split
  between `jdr_transcriptions` and `jdr_chunks`.
- **Pedagogy over output volume**: PASS. The plan uses one narrow persistence
  change and one endpoint, with tests tied directly to the handoff ACs.
- **YAGNI**: PASS. No structured editing, speaker-label editing, export-mode
  choices, or reset/delete endpoint are included in BD-13.
- **Strict separation of concerns**: PASS. Business behavior stays in
  `app/services/jdr/logic.py`; HTTP translation stays in the router; LLM calls
  remain behind the existing adapter.
- **Test discipline**: PASS. Public endpoint, contract exposure, read fallback,
  ownership/state errors, and generation source selection are all testable.
- **Security by default**: PASS. The endpoint reuses existing GM auth and
  session ownership/campaign scoping; cross-owner sessions remain hidden as
  not found.
- **12-Factor**: PASS. No secrets or new runtime configuration are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/013-transcription-edit/
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
+-- services/
|   +-- jdr/
|       +-- schemas.py          # Add transcription edit request/response schemas
|       +-- logic.py            # Add save/read edited transcription operations
|       +-- router.py           # Add PUT /sessions/{id}/transcription
|       +-- db/
|           +-- models.py       # Add nullable edited transcript field on Session
|           +-- repositories.py # Add focused session update/read helper if useful
+-- jobs/
|   +-- jdr.py                  # Prefer edited transcript in generation sources

migrations/
+-- versions/
|   +-- 0010_session_transcription_edit.py

tests/
+-- services/
|   +-- jdr/
|       +-- test_transcription_edit.py # Endpoint/read/contract coverage
+-- jobs/
|   +-- test_jdr_summary.py            # Edited source for summary map-reduce

docs/
+-- context/
|   +-- api/
|       +-- openapi.json        # Regenerate frontend contract
+-- services/
|   +-- jdr.md                  # Document editable transcription behavior
+-- memo.md                    # Add quick endpoint/migration reminder
+-- journal.md                 # BD-13 learning entry after implementation
```

**Structure Decision**: Use the existing JDR service and job module. The feature
is session-level business behavior, so adding a new service or generic document
editing abstraction would exceed the current handoff.

## Phase 0: Research

Research output: [`research.md`](./research.md)

Resolved decisions:

- Store one nullable Markdown override on `jdr_sessions`.
- Expose `PUT /services/jdr/sessions/{session_id}/transcription` because the
  submitted payload replaces the whole edited Markdown override.
- Return a small JSON projection from the write endpoint so the frontend can
  confirm the stored content and edited state without an immediate second read.
- Reject save attempts unless the owned session is already `transcribed`.
- Treat empty or whitespace-only `content_md` as invalid input.
- Preserve original `jdr_transcriptions` and `jdr_chunks` rows.
- Update summary map-reduce to prefer edited Markdown as raw source text.
- Leave explicit reset/delete outside BD-13.

## Phase 1: Design

Design outputs:

- [`data-model.md`](./data-model.md)
- [`contracts/rest-api.md`](./contracts/rest-api.md)
- [`quickstart.md`](./quickstart.md)

Implementation shape:

1. Add migration `0010_session_transcription_edit.py` with nullable
   `jdr_sessions.edited_transcript_md`.
2. Add `Session.edited_transcript_md` to the ORM model.
3. Add request/response schemas, tentatively `TranscriptionEditIn` and
   `TranscriptionEditOut`, with `content_md` required and non-blank.
4. Add a logic operation that loads the session through the same GM/campaign
   ownership scope as existing reads, checks `state=transcribed`, stores the
   Markdown override, commits, and returns the projection.
5. Add `PUT /services/jdr/sessions/{session_id}/transcription` with existing GM
   auth and error categories: `404 session-not-found`, `409
   session-not-transcribed`, `422 validation_error`.
6. Update `GET /services/jdr/sessions/{session_id}/transcription.md` to return
   `edited_transcript_md` when present before falling back to current automatic
   rendering. Keep the JSON diarised route unchanged.
7. Update the shared generation source path in `app/jobs/jdr.py` so narrative,
   elements, and POV jobs prefer `edited_transcript_md` before falling back to
   the current mode-specific automatic source.
8. Update `_generate_summary` so the non-diarised summary map-reduce uses
   transient chunks derived from `edited_transcript_md` when present; original
   `jdr_chunks.text` rows remain unchanged.
9. Ensure derived narrative/elements/POV generation still works after summary
   regeneration. BD-13 does not introduce automatic cascade invalidation on
   edit.
10. Add endpoint tests, worker tests, migration/model tests as needed, and
   OpenAPI assertions.
11. Regenerate `docs/context/api/openapi.json` and update JDR docs/memo/journal.

## Phase 1 Constitution Re-check

- **YAGNI**: PASS. The design still stores one override and exposes one write
  endpoint. Reset/delete and structured editing remain deferred.
- **Separation of concerns**: PASS. Persistence/model work stays in the JDR DB
  layer; session business rules stay in logic; route code only translates HTTP.
- **Test discipline**: PASS. The plan names targeted endpoint, contract, and
  worker tests matching the handoff acceptance criteria.
- **Security/12-Factor**: PASS. Existing auth/config patterns are reused; no new
  secret/config surface is introduced.

## Complexity Tracking

No constitution violations.
