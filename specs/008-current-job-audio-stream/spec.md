# Feature Specification: Current Job and Audio Stream

**Feature Branch**: `main`  
**Created**: 2026-06-03  
**Status**: Draft  
**Input**: User description: "BD-8 current_job_id on SessionOut, audio stream access, and irreversible audio deletion behavior"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Resume Transcription Tracking After Refresh (Priority: P1)

As a GM viewing a JDR session, I need the session detail to expose the active or most recent transcription job so that the interface can resume tracking progress after a browser refresh without relying on local page state.

**Why this priority**: This unblocks Story 3.4. Without it, a refreshed page can remain stuck in a transcription state and never converge to success or failure.

**Independent Test**: Start a transcription, refresh the session detail while the session is processing, and verify that the session response still contains the job reference needed to resume status tracking.

**Acceptance Scenarios**:

1. **Given** a session with a newly uploaded audio file, **When** the session detail is retrieved, **Then** it includes the current transcription job identifier.
2. **Given** a transcription job that succeeds, **When** the session detail is retrieved afterward, **Then** the job identifier remains available for the user interface to inspect the final job status.
3. **Given** a transcription job that fails, **When** the session detail is retrieved afterward, **Then** the job identifier remains available so the user interface can display the failure context.
4. **Given** a session whose audio has been purged, **When** the session detail is retrieved, **Then** no current transcription job identifier is exposed.

---

### User Story 2 - Play and Seek Session Audio (Priority: P1)

As a GM or authorized campaign member viewing a session, I need to play the uploaded audio and seek within it so that I can review moments from the session without downloading the file manually.

**Why this priority**: This unblocks the audio player flow for Story 3.5.

**Independent Test**: Open a session with uploaded audio in a browser audio player, start playback, jump to a later timestamp, and verify playback resumes from the requested point.

**Acceptance Scenarios**:

1. **Given** an authorized user and a session with audio, **When** the audio is requested, **Then** the user receives the audio content with its real media type and length.
2. **Given** the browser requests only a byte range of the audio, **When** the range is valid, **Then** the response contains only that range and identifies the returned byte interval.
3. **Given** the session has no audio or the user is not authorized for that campaign, **When** the audio is requested, **Then** the response is treated as not found by the caller.

---

### User Story 3 - Replace Audio Deliberately and Irreversibly (Priority: P2)

As a GM, I need audio deletion to fully reset a session for replacement, while preventing deletion during active transcription, so that destructive replacement is explicit and does not corrupt in-flight work.

**Why this priority**: This supports Story 3.6 and clarifies the product decision that replacing audio permanently removes previous derived artifacts.

**Independent Test**: Delete audio from sessions in each relevant lifecycle state and verify the allowed states reset fully while active transcription is refused.

**Acceptance Scenarios**:

1. **Given** a session with uploaded or completed audio, **When** the GM deletes the audio, **Then** the audio, transcription, intermediate chunks, derived artifacts, and current job reference are removed and the session returns to a created state.
2. **Given** a session whose transcription is actively running, **When** the GM tries to delete the audio, **Then** deletion is refused and the session remains unchanged.
3. **Given** a session already in a created state, **When** the GM deletes the audio again, **Then** the operation completes without creating new side effects.
4. **Given** a session with a failed transcription, **When** the GM views the session, **Then** the source audio is still available for replay or retry unless the GM explicitly deletes it.

### Edge Cases

- A completed or failed transcription job may expire from the job backend while the session still references it; the session must remain readable and must not expose a broken required relationship.
- Re-uploading audio after a destructive delete must behave like a fresh upload and create a new current transcription job reference.
- Invalid audio range requests must return a clear range error and must not send the full audio by accident.
- Audio retrieval for a session in another tenant or campaign must not reveal whether the audio exists.
- Deleting audio must be safe to repeat when no audio remains.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Session detail responses MUST include an optional current transcription job identifier.
- **FR-002**: The current transcription job identifier MUST be set when audio upload starts a transcription pipeline.
- **FR-003**: The current transcription job identifier MUST remain available after transcription success or failure until the audio is explicitly purged or the job reference is no longer valid.
- **FR-004**: The current transcription job identifier MUST be absent when a session has never had audio uploaded or after its audio has been purged.
- **FR-005**: Authorized campaign users MUST be able to retrieve the stored audio for a session that has audio.
- **FR-006**: Audio retrieval MUST support partial byte retrieval so browser playback controls can seek within the file.
- **FR-007**: Audio retrieval MUST expose enough metadata for a browser audio player to understand the media type, total size, and returned byte range.
- **FR-008**: Audio retrieval MUST return a not-found outcome when no audio exists or when the caller is not authorized to access the session.
- **FR-009**: Audio deletion MUST permanently remove the source audio, intermediate audio chunks, produced transcription, derived artifacts tied to that audio, and the current transcription job reference.
- **FR-010**: Audio deletion MUST reset the session to the initial created state when deletion is allowed.
- **FR-011**: Audio deletion MUST be allowed for uploaded, transcribed, and failed sessions.
- **FR-012**: Audio deletion MUST be refused while a transcription is actively running.
- **FR-013**: Audio deletion MUST be repeatable on a session that has no audio without creating errors or extra state changes.
- **FR-014**: A failed transcription MUST preserve the source audio and current job reference so the user can inspect failure context and retry without re-uploading.
- **FR-015**: Existing session list and session detail behavior MUST remain backward compatible except for the additive current job field and clarified audio behaviors.

### Key Entities

- **Session**: A JDR session belonging to a campaign; includes lifecycle state, audio presence, transcription result, timestamps, and an optional current transcription job identifier.
- **Transcription Job**: The current or most recent processing job for a session audio transcription; includes status and failure context used by the user interface.
- **Session Audio**: The stored source audio associated with a session; may be streamed, partially retrieved, or permanently purged.
- **Derived Audio Artifacts**: Intermediate chunks, transcription output, and any generated content tied to a specific audio file and removed during destructive replacement.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After a browser refresh during transcription, the user interface can resume progress tracking from session data alone in 100% of tested active transcription cases.
- **SC-002**: Users can seek within uploaded session audio through the browser player in 100% of tested sessions with audio.
- **SC-003**: Audio retrieval starts returning playable data in under 2 seconds for locally stored session audio in normal development conditions.
- **SC-004**: Destructive audio replacement leaves no previous audio, transcription, chunk, or derived artifact visible in 100% of tested allowed deletion states.
- **SC-005**: Active transcription deletion attempts are refused in 100% of tested in-flight cases and leave the session unchanged.
- **SC-006**: Existing session detail consumers continue to work without requiring changes, because the job reference is additive and optional.

## Assumptions

- The target users are authenticated JDR users who already have campaign-level authorization.
- The frontend preference is direct authenticated audio streaming with byte ranges rather than a short-lived signed URL.
- Cross-campaign or cross-tenant access failures should not reveal resource existence and should be treated like not found.
- Failed transcription keeps the source audio so the frontend can offer retry without forcing a new upload.
- Epic 4 multi-artifact job listing is intentionally out of scope for this feature; this feature only tracks the transcription pipeline job.
