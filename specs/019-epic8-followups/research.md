# Research: Epic 8 Follow-ups

## Decision 1: Guard manual artifact edits with the current artifact job state

**Decision**: Before each manual artifact edit, inspect the session's current job and reject the edit when that job is an active artifact-generation job (`summary`, `narrative`, `elements`, or `povs`).

**Rationale**: The lost-update risk exists when a worker can still write artifact rows after the request-time edit. The project already treats Redis/RQ as the live job-state source, while SQL is a projection. Reusing that model keeps behavior consistent with existing job polling and delete/recover flows.

**Alternatives considered**:

- Lock rows until generation finishes: rejected because LLM jobs are long-running and holding database locks across them violates the existing non-blocking worker design.
- Add optimistic versioning to artifacts: rejected for this follow-up because current public contracts do not expose versions and the issue asks only for an in-flight guard.
- Block on any current job: rejected because issue scope is artifact lost updates; non-artifact jobs should keep existing behavior.

## Decision 2: Confirm empty elements replacement explicitly

**Decision**: Keep full replacement semantics for elements, but reject `elements: []` unless the caller passes an explicit clear confirmation.

**Rationale**: Requiring at least one element would prevent intentional clearing. A confirmation flag keeps the API expressive while preventing accidental full-card wipe.

**Alternatives considered**:

- Always reject empty elements: rejected because an intentional "clear card" is a plausible edit.
- Add per-element CRUD: rejected as over-engineering and contrary to the current atomic full-replace contract.

## Decision 3: Make player participation mode-aware

**Decision**: `diarised` sessions continue to use speaker-to-PJ mapping; `non_diarised` sessions use the flat player-presence list for player reads and player session listing.

**Rationale**: `non_diarised` sessions do not have meaningful speaker labels. The project already introduced `jdr_session_players` as the equivalent participation declaration for that mode.

**Alternatives considered**:

- Keep only speaker mapping: rejected because it leaves `non_diarised` player reads undefined.
- Grant all players in the campaign access to non-diarised shared artifacts: rejected because it weakens existing least-privilege player scoping.

## Decision 4: Align artifact edit provenance defaults without migration

**Decision**: Align the ORM default for `Artifact.is_edited` with the existing migration-level false default; no new migration is required.

**Rationale**: Migration 0019 is authoritative for existing databases. The follow-up is a model consistency cleanup for fresh metadata generation and developer expectations.

**Alternatives considered**:

- Add a new migration: rejected because there is no schema change to apply.
- Leave model mismatch in place: rejected because it keeps avoidable drift between model and migration.

## Decision 5: Add a generous hard cap for text edits

**Decision**: Add a documented upper bound measured in characters/bytes high enough to accept at least 10,000-word RPG artifacts and low enough to reject pathological payloads.

**Rationale**: BD-25 intentionally allows long text, but "unbounded" does not need to mean "unlimited payload size". A multi-megabyte guard protects storage and request handling while preserving normal use.

**Alternatives considered**:

- Keep no cap: rejected because issue #28 explicitly asks for optional hardening and the user requested implementation of the issue.
- Use a small editorial cap: rejected because it would violate BD-25's long-form editing requirement.
