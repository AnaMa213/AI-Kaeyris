# Feature Specification: Epic 8 Follow-ups

**Feature Branch**: `codex/028-epic8-followups`  
**Created**: 2026-06-30  
**Status**: Draft  
**Input**: User description: "GitHub issue #28 - Epic 8 code-review follow-ups (artifact editing & player reads)"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Prevent Concurrent Edit Loss (Priority: P1)

As a MJ, I want manual artifact edits to be rejected while an artifact generation or regeneration is still running, so that a late worker write cannot silently overwrite my edit.

**Why this priority**: This prevents lost updates on manually edited session artifacts, which is the highest-impact data integrity risk in the issue.

**Independent Test**: Seed a session with an existing artifact and an active artifact job, attempt each manual edit operation, and verify the edit is rejected while the stored artifact remains unchanged.

**Acceptance Scenarios**:

1. **Given** a session has an active summary, narrative, elements, or POV generation job, **When** the MJ attempts to edit a generated artifact for that session, **Then** the system rejects the edit with a conflict and preserves the existing artifact content.
2. **Given** the active artifact job has finished or no longer exists, **When** the MJ edits an existing artifact, **Then** the edit succeeds and manual-edit provenance is recorded.
3. **Given** a non-artifact job is active for the session, **When** the MJ edits an artifact, **Then** the artifact edit is governed only by the existing artifact-edit rules for that endpoint.

---

### User Story 2 - Prevent Accidental Elements Wipe (Priority: P1)

As a MJ, I want the elements card to reject an accidental empty replacement unless I explicitly confirm the clear action, so that a mistaken empty payload does not erase useful session notes.

**Why this priority**: The elements card uses full replacement semantics; an empty list can otherwise wipe the whole card silently.

**Independent Test**: Attempt to replace a non-empty elements card with an empty list both without and with explicit confirmation, verifying that only the confirmed clear succeeds.

**Acceptance Scenarios**:

1. **Given** an elements artifact already contains entries, **When** the MJ submits an empty elements replacement without confirming a full clear, **Then** the system rejects the request as invalid and preserves the existing card.
2. **Given** the MJ explicitly confirms a full clear, **When** the MJ submits an empty elements replacement, **Then** the system accepts the request and stores an empty elements card with manual-edit provenance.
3. **Given** the MJ submits one or more valid elements, **When** the MJ replaces the card, **Then** the replacement succeeds as before.

---

### User Story 3 - Define Player Scope In Non-Diarised Sessions (Priority: P2)

As a player, I want access to shared artifacts for non-diarised sessions to follow the player-presence list, so that my player view is predictable even when there is no speaker-to-character mapping.

**Why this priority**: The existing player-read routes rely on participation scoping; non-diarised sessions use a different participation declaration and need a clear backend rule before the frontend can promise the UX.

**Independent Test**: Create a non-diarised session with a player declared as present, then verify the linked player can read shared artifacts for that session and cannot read shared artifacts for sessions where their character is not present.

**Acceptance Scenarios**:

1. **Given** a non-diarised session lists a player's character as present, **When** that player reads shared artifacts for the session, **Then** the system grants access if the artifact exists.
2. **Given** a non-diarised session does not list the player's character as present, **When** that player reads shared artifacts for the session, **Then** the system rejects access.
3. **Given** a diarised session uses speaker-to-character mapping, **When** player reads are evaluated, **Then** existing diarised scoping behavior remains unchanged.

---

### User Story 4 - Keep Artifact Defaults Consistent (Priority: P3)

As an operator, I want newly created artifact rows to have the same default manual-edit state in every supported environment, so that fresh and migrated deployments behave consistently.

**Why this priority**: This is a low-risk cleanup, but it prevents environment-specific surprises around manual-edit provenance.

**Independent Test**: Create a new artifact without manually setting edit provenance and verify it is treated as unedited across supported database backends.

**Acceptance Scenarios**:

1. **Given** a new artifact is created by generation, **When** no manual-edit fields are supplied, **Then** the artifact is considered not manually edited.
2. **Given** an artifact is manually edited, **When** provenance is recorded, **Then** the edit state still takes precedence over the default.

---

### User Story 5 - Bound Pathological Text Edits (Priority: P4)

As an operator, I want artifact text edits to allow long RPG content while rejecting pathological payloads, so that normal long-form editing remains possible without unlimited storage growth from accidental or abusive requests.

**Why this priority**: This is hardening only; BD-25 intentionally supports long text and must not regress.

**Independent Test**: Submit a long realistic artifact edit and a payload above the documented safety limit, verifying the realistic edit succeeds and the pathological payload is rejected with a validation error.

**Acceptance Scenarios**:

1. **Given** a MJ submits a long artifact text expected for RPG summaries, **When** the text is within the generous safety limit, **Then** the edit succeeds without truncation.
2. **Given** a MJ submits an artifact text above the safety limit, **When** the request is validated, **Then** the system rejects it clearly and does not modify the artifact.

---

### Edge Cases

- An active job id may be stale or missing from the queue backend; stale terminal or missing jobs must not permanently block edits.
- Multiple artifact kinds share the same session; any active artifact generation for that session can rewrite artifacts and must block manual artifact edits.
- Empty elements replacement must preserve the existing card when rejected.
- Non-diarised player reads must not accidentally grant access through diarised speaker mappings that are not meaningful for that mode.
- Text length validation must not truncate accepted content.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST reject manual artifact edits while the target session has an active artifact generation or regeneration job.
- **FR-002**: System MUST preserve existing artifact content and provenance when an edit is rejected because a generation job is active.
- **FR-003**: System MUST allow manual artifact edits again once the relevant active artifact job is terminal or absent.
- **FR-004**: System MUST reject an empty elements-card replacement unless the caller explicitly confirms a full clear.
- **FR-005**: System MUST preserve the existing elements card when an unconfirmed empty replacement is rejected.
- **FR-006**: System MUST allow a confirmed empty elements-card replacement and record it as a manual edit.
- **FR-007**: System MUST grant non-diarised player reads based on the session player-presence list.
- **FR-008**: System MUST keep existing diarised player-read scoping based on speaker-to-character mapping.
- **FR-009**: System MUST keep generated artifacts unedited by default in fresh deployments and migrated deployments.
- **FR-010**: System MUST keep accepting long artifact text edits expected for RPG summaries without truncation.
- **FR-011**: System MUST reject artifact text edits above a documented generous safety limit without modifying the artifact.

### Key Entities

- **Artifact**: A generated or manually edited session output such as summary, narrative, elements, or POV, with manual-edit provenance.
- **Artifact Generation Job**: An asynchronous operation that can create or replace one or more artifacts for a session.
- **Elements Card**: A structured list of notable session elements that is replaced as a whole during manual editing.
- **Player Participation**: The rule that determines whether a player can read shared session artifacts; speaker mapping for diarised sessions, player-presence list for non-diarised sessions.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of manual artifact edit attempts during active artifact generation are rejected without changing stored artifact content.
- **SC-002**: An accidental empty elements replacement without explicit confirmation never clears an existing card.
- **SC-003**: Players can read shared artifacts for 100% of sessions where their character is declared present or mapped, and cannot read sessions where they are not declared present or mapped.
- **SC-004**: Long realistic artifact edits of at least 10,000 words remain accepted without truncation.
- **SC-005**: Payloads above the documented safety limit are rejected before changing stored artifact content.

## Assumptions

- The existing authentication and session ownership rules remain unchanged.
- "Shared artifacts" for player reads means summary, narrative, and elements; player POV remains scoped to the requesting player's own character.
- The explicit elements clear confirmation can be represented in the existing request surface without introducing a new artifact type.
- The safety limit for text edits should be generous enough for real RPG artifacts and documented for frontend validation.
