# Feature Specification: Delete JDR Session

**Feature Branch**: `codex/015-delete-session`
**Created**: 2026-06-09
**Status**: Draft
**Input**: Backend handoff BD-15 asks for a real persistent deletion flow for a JDR session so the campaign page can delete sessions without a misleading frontend-only mock. Deleting a session must remove its dependent data and stored audio, preserve GM isolation, update campaign session counts, and expose the contract needed by the frontend.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Delete Own Session (Priority: P1)

As a GM viewing a campaign, I want to permanently delete one of my sessions so that obsolete or mistaken sessions disappear from the campaign instead of staying visible forever.

**Why this priority**: This is the core product need. The frontend deliberately avoided a destructive local mock because a session carries audio, transcription, generated artifacts, and job state.

**Independent Test**: Can be tested by creating a session for a GM, deleting it, then confirming the delete succeeds, direct reads return not found, list reads no longer include it, and the campaign count decreases.

**Acceptance Scenarios**:

1. **Given** a GM owns a session in a campaign, **When** the GM deletes that session, **Then** the system confirms deletion without response content.
2. **Given** a deleted session, **When** the GM tries to read it directly, **Then** the system reports that the session is not found.
3. **Given** a deleted session belonged to a campaign, **When** the GM lists campaign sessions or reads campaign summary data, **Then** the deleted session is absent and the session count reflects the deletion.

---

### User Story 2 - Remove Dependent Session Data (Priority: P2)

As a GM, I need deleting a session to remove its dependent data so that old audio, transcription material, player presence, speaker mapping, and generated artifacts do not remain accessible or orphaned.

**Why this priority**: A session is a heavy aggregate. Deleting only the top-level row would be misleading and would leave private or obsolete campaign material behind.

**Independent Test**: Can be tested by creating a session with stored audio, transcription data, chunks, player or mapping data, and artifacts, deleting the session, then verifying those related records and files are gone or no longer accessible.

**Acceptance Scenarios**:

1. **Given** a session has stored audio, **When** the session is deleted, **Then** the stored audio is removed and no audio read remains available for that session.
2. **Given** a session has transcription, chunks, mapping, players, or generated artifacts, **When** the session is deleted, **Then** those dependent resources are removed with the session.
3. **Given** a session had a manually edited transcription, **When** the session is deleted, **Then** the edited transcript does not remain accessible through any session read.

---

### User Story 3 - Preserve Visibility And In-Flight Work Safety (Priority: P3)

As a GM or frontend maintainer, I need deletion to preserve existing authorization behavior and to behave deterministically when work is still in progress.

**Why this priority**: Deletion must not leak another GM's sessions and must not silently race against transcription or artifact generation jobs.

**Independent Test**: Can be tested by trying to delete a foreign session and by trying to delete a session with active work, then confirming the returned outcomes are deterministic and documented.

**Acceptance Scenarios**:

1. **Given** a GM tries to delete another GM's session, **When** the delete is requested, **Then** the session remains hidden as not found.
2. **Given** a player credential tries to delete a GM session, **When** the delete is requested, **Then** the request is rejected by the existing GM-only access policy.
3. **Given** a session has active transcription or generation work, **When** deletion is requested, **Then** deletion is refused with a clear conflict and the session remains intact.

### Edge Cases

- The session id is unknown, malformed, expired, or belongs to another GM.
- The session has no audio, transcription, mapping, players, or artifacts yet.
- The session has some, but not all, dependent resources.
- Stored audio file is already missing from disk when deletion is requested.
- The session has a current or active job that could still write data.
- Campaign session counts are read immediately after deletion.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST allow an authenticated GM to delete a session they are allowed to manage.
- **FR-002**: A successful deletion MUST complete without response content.
- **FR-003**: The system MUST return the existing not-found outcome when the session does not exist or is not visible to the current GM.
- **FR-004**: The system MUST reject non-GM credentials using the existing GM-only access policy.
- **FR-005**: Deleting a session MUST remove it from direct session reads and session lists.
- **FR-006**: Deleting a session MUST update campaign summary counts that include sessions.
- **FR-007**: Deleting a session MUST remove dependent audio, transcription segments, chunks, generated artifacts, player presence, speaker mapping, and manual transcription edits for that session.
- **FR-008**: Deleting a session MUST tolerate already-missing stored audio files without leaving the session undeleted.
- **FR-009**: Deleting a session MUST avoid orphaning session-scoped records.
- **FR-010**: A session with active transcription or artifact work MUST NOT be deleted; the system MUST return a clear conflict outcome and preserve the session.
- **FR-011**: Existing create, list, read, and update session behavior MUST remain unchanged for non-deleted sessions.
- **FR-012**: The public contract documentation MUST expose the delete session operation and its success and error outcomes.
- **FR-013**: The feature MUST NOT require frontend-side mock deletion or hidden local state to make the campaign page behave correctly.

### Key Entities

- **Session**: A campaign session managed by a GM, including title, date, state, campaign context, current work marker, and optional manual transcript override.
- **Session Dependencies**: Data owned by a session, including stored audio, transcription records, chunks, generated artifacts, player presence, and speaker mapping.
- **Campaign Summary**: Campaign data shown to the GM, including the count of sessions after deletion.
- **Active Work**: Transcription or artifact generation that may still write data for the session.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of tested owned session deletions return an empty success response and make the session unreadable afterward.
- **SC-002**: 100% of tested deleted sessions disappear from session lists and decrement campaign session count by one.
- **SC-003**: 100% of tested dependent resources attached to a deleted session are removed or become inaccessible.
- **SC-004**: 100% of tested foreign or unknown session deletion attempts avoid exposing session details.
- **SC-005**: 100% of tested active-work deletion attempts return a conflict and leave the session readable.
- **SC-006**: Existing tested session create, list, read, and update flows continue to pass without response contract changes.

## Assumptions

- Deletion is permanent for the current feature; no restore or trash behavior is included.
- Active work is blocked with a conflict rather than silently canceled because no existing reliable job cancellation contract is available.
- A missing audio file during deletion should not block deletion when the session itself is otherwise valid.
- Historical RQ job metadata may expire independently; the deletion decision is based on the session's current work marker and observable active job state.
- Player characters themselves are not deleted; only their presence or mapping relation to the deleted session is removed.
