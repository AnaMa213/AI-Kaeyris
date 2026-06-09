# Feature Specification: Transcription Edit

**Feature Branch**: `codex/013-transcription-edit`  
**Created**: 2026-06-09  
**Status**: Draft  
**Input**: Backend handoff BD-13 asks to let a GM persist a manually corrected Markdown transcription for a transcribed JDR session. The edited transcription must be returned by Markdown transcription reads and must become the source used by later artifact generation, while preserving the original automatic transcription data.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Save And Reuse An Edited Transcription (Priority: P1)

As a GM reviewing an automatically transcribed JDR session, I need to correct the transcription manually and save that edited text so that later reads and downloads show the corrected version instead of the flawed automatic text.

**Why this priority**: Manual correction is the core frontend blocker. Without persisted edits, the edit screen is only temporary and the GM loses corrections on refresh or download.

**Independent Test**: Can be tested by starting from a transcribed session, saving corrected Markdown text, then reading the Markdown transcription again and confirming it matches the saved text.

**Acceptance Scenarios**:

1. **Given** a GM owns a transcribed session with an automatically rendered Markdown transcription, **When** the GM saves corrected Markdown text for that session, **Then** the corrected text is persisted for that session.
2. **Given** a corrected Markdown transcription was saved, **When** the GM reads or downloads the Markdown transcription again, **Then** the corrected text is returned.
3. **Given** no corrected Markdown transcription exists for a transcribed session, **When** the GM reads or downloads the Markdown transcription, **Then** the existing automatically rendered text is returned.

---

### User Story 2 - Generate Artifacts From Corrected Text (Priority: P2)

As a GM, I need summaries and generated artifacts created after my manual correction to use the edited transcription so that AI output reflects what was actually said at the table.

**Why this priority**: The product value of correction depends on generation quality. If generated summaries continue to use the automatic transcription, editing is cosmetic and does not solve the user problem.

**Independent Test**: Can be tested by saving an edited transcription containing a distinctive correction, launching a summary generation afterward, and confirming the generated source content uses the corrected text rather than the automatic text.

**Acceptance Scenarios**:

1. **Given** a GM has saved an edited transcription for a session, **When** the GM launches a summary generation after the edit, **Then** the generation uses the edited transcription as its source.
2. **Given** no edited transcription exists for a session, **When** the GM launches a summary generation, **Then** the existing automatic transcription source is used.
3. **Given** an edited transcription replaces an earlier edited version, **When** the GM launches a later generation, **Then** the latest saved edited text is used.

---

### User Story 3 - Protect Session State And Ownership (Priority: P3)

As a GM, I must only be able to edit transcriptions for my own sessions that are ready for review so that incomplete or unauthorized session data cannot be changed.

**Why this priority**: Editing must preserve the existing campaign/session isolation and avoid creating corrections for sessions that do not yet have a completed transcription.

**Independent Test**: Can be tested by attempting to save edited text for an untranscribed session and for another GM's session, then confirming both attempts are rejected and no transcription content changes.

**Acceptance Scenarios**:

1. **Given** a session has not completed transcription, **When** a GM attempts to save edited Markdown text, **Then** the edit is rejected with a clear not-ready outcome and no edited text is persisted.
2. **Given** a session belongs to another GM, **When** the current GM attempts to save edited Markdown text, **Then** the session remains hidden or unavailable and no content changes.
3. **Given** a GM submits empty or whitespace-only edited text, **When** the save is attempted, **Then** the edit is rejected so the existing transcription is not accidentally replaced with unusable content.

### Edge Cases

- A transcribed session has no edited transcription yet.
- A transcribed session already has an edited transcription and the GM saves a replacement.
- A session is not yet transcribed when the GM attempts to save edited text.
- A GM attempts to edit a session owned by another GM.
- Edited text is empty or only whitespace.
- Edited text is valid Markdown regardless of whether the original transcription was diarised or non-diarised.
- Existing automatic chunks and diarised segments must remain unchanged after an edit.
- Explicit reset or deletion of the edited transcription is intentionally out of scope for BD-13.
- Structured editing of chunks, segments, speaker labels, export format choices, and transcription mode selection are intentionally out of scope for BD-13.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: GMs MUST be able to save edited Markdown transcription text for a transcribed session they own.
- **FR-002**: Saving edited Markdown text MUST replace any previous edited Markdown text for the same session.
- **FR-003**: Reading or downloading the Markdown transcription MUST return the edited text when an edited version exists.
- **FR-004**: Reading or downloading the Markdown transcription MUST keep returning the automatic transcription when no edited version exists.
- **FR-005**: Summary and artifact generations launched after an edit MUST use the latest edited transcription text as their source.
- **FR-006**: Summary and artifact generations MUST keep using the automatic transcription source when no edited transcription exists.
- **FR-007**: Saving edited text MUST be allowed for both diarised and non-diarised sessions once transcription is complete.
- **FR-008**: Saving edited text MUST be rejected for sessions whose transcription is not complete.
- **FR-009**: Saving edited text MUST preserve ownership isolation: a GM cannot edit another GM's session transcription.
- **FR-010**: Empty or whitespace-only edited text MUST be rejected.
- **FR-011**: Saving edited text MUST NOT modify the original automatic chunks, segments, speaker attribution, or transcription mode data.
- **FR-012**: The write capability and edited-text input MUST be discoverable by the frontend contract generation workflow.
- **FR-013**: Explicit reset/deletion of an edited transcription MUST NOT be included in BD-13.

### Key Entities

- **JDR Session**: A game session owned by a GM, with a transcription lifecycle state and existing transcription reads.
- **Automatic Transcription**: The original generated transcription data and rendered Markdown fallback for a session.
- **Edited Transcription**: The latest GM-saved Markdown correction for a session, separate from the automatic transcription data.
- **Generation Source Text**: The text selected for summaries and generated artifacts; it prefers the edited transcription when present and otherwise uses the automatic transcription.
- **GM**: The session owner allowed to review, edit, and generate artifacts for their own sessions.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A GM can save corrected Markdown for a transcribed owned session and retrieve the exact corrected Markdown on the next transcription read.
- **SC-002**: 100% of tested Markdown transcription reads return the edited version when it exists and the automatic version when it does not.
- **SC-003**: 100% of tested summary generations launched after an edit consume the latest edited text instead of the automatic transcription.
- **SC-004**: 100% of tested untranscribed-session and cross-owner edit attempts are rejected without changing persisted transcription content.
- **SC-005**: The frontend contract generation workflow can discover the transcription edit operation and its edited Markdown input.
- **SC-006**: Existing automatic transcription reads continue to work for sessions with no edited transcription.

## Assumptions

- BD-13 adopts the recommended override Markdown approach: one edited Markdown text per session, separate from automatic transcription data.
- The existing GM/session ownership model remains the authorization boundary.
- Existing Markdown transcription reads/downloads remain the user-visible way to retrieve transcription text.
- Existing automatic transcription data remains the fallback and is not rewritten by this feature.
- Explicit reset of edited text is deferred to a future backend handoff unless requested separately.
