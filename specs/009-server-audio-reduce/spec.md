# Feature Specification: Server Audio Reduce

**Feature Branch**: `codex/009-server-audio-reduce`
**Created**: 2026-06-03
**Status**: Draft
**Input**: User description: "BD-9 backend handoff: move JDR session audio reduction from the browser to the server-side processing pipeline so full raw uploads can be accepted before transcription."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Upload Long Session Audio Without Client Reduction (Priority: P1)

As a GM preparing a JDR session transcript, I need to upload a full raw session recording without reducing it in the browser so that long sessions are not blocked by client device limits or browser-specific requirements.

**Why this priority**: This is the core user value. The frontend has already stopped relying on browser reduction, so the remaining limitation is that long raw recordings can fail before entering the transcription flow.

**Independent Test**: Upload a raw JDR session recording within the supported product limit and verify that the session accepts the upload, creates the usual transcription tracking reference, and enters the normal transcription flow.

**Acceptance Scenarios**:

1. **Given** an authenticated GM and a JDR session ready for audio, **When** the GM uploads a raw session recording within the supported limit, **Then** the upload is accepted and the session enters transcription processing.
2. **Given** a supported raw recording that would previously have exceeded the transcription provider's direct file limit, **When** the GM uploads it, **Then** the system prepares it for transcription before sending it to transcription processing.
3. **Given** the upload is accepted, **When** the frontend polls the session's current processing job, **Then** it can continue using the existing transcription job flow without tracking a separate user-visible reduction job.

---

### User Story 2 - Keep the Existing Frontend Contract Stable (Priority: P1)

As the frontend application, I need audio upload responses and polling behavior to remain stable so that moving reduction to the server does not require a new upload entry point, new visible job type, or new state label for users.

**Why this priority**: The frontend has already adapted to raw uploads and expects one current job to poll. Keeping the contract stable reduces coordination cost and lowers regression risk.

**Independent Test**: Submit audio through the existing upload flow and verify that the response shape, session state progression, and job kind exposed to the frontend remain compatible with current frontend expectations.

**Acceptance Scenarios**:

1. **Given** a raw audio upload succeeds, **When** the upload response is returned, **Then** it exposes the same user-facing upload result fields as before, including the job reference.
2. **Given** reduction and transcription are both pending or running, **When** the frontend asks for current session status, **Then** it sees the session as part of the existing transcription lifecycle rather than a new reduction lifecycle.
3. **Given** the system rejects a file because it exceeds the supported server-side limit, **When** the frontend receives the error, **Then** the error clearly communicates the effective limit so the user can shorten or reduce the recording.

---

### User Story 3 - Manage Raw and Reduced Audio Deliberately (Priority: P2)

As a platform owner, I need raw uploaded audio to be removed after successful preparation unless it is still needed for failure recovery, so that long recordings do not consume unnecessary storage.

**Why this priority**: Raw session recordings can be much larger than prepared audio. Storage discipline matters on a Raspberry Pi-hosted platform, but it should not compromise recovery from preparation or transcription failures.

**Independent Test**: Process successful and failed uploads, then verify which audio artifacts remain visible through session behavior and cleanup semantics.

**Acceptance Scenarios**:

1. **Given** a raw upload is successfully prepared for transcription, **When** preparation completes, **Then** the raw source recording is no longer retained for normal session use.
2. **Given** audio preparation fails before transcription can start, **When** the GM reviews the session, **Then** the system exposes a clear failure outcome and avoids presenting the session as successfully transcribing.
3. **Given** the GM deletes session audio after processing, **When** deletion completes, **Then** any retained raw audio, prepared audio, transcription, and derived artifacts from that upload are removed together.

### Edge Cases

- A raw upload exceeds the supported server-side size limit.
- Audio preparation succeeds but transcription fails afterward.
- Audio preparation fails before any transcription attempt can begin.
- The user refreshes or leaves the page while audio preparation is running.
- The same session receives a second upload attempt while an upload or transcription is already active.
- The system cannot determine audio duration at upload time.
- Cleanup after successful preparation fails partially and must not leave the session in a misleading success state.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST accept raw JDR session audio uploads up to a documented product limit of 500 MB unless the deployment documents a lower effective limit.
- **FR-002**: The system MUST return a clear too-large error when a raw audio upload exceeds the effective supported limit.
- **FR-003**: The system MUST prepare accepted raw audio into a transcription-ready representation before transcription processing begins.
- **FR-004**: Audio preparation MUST be part of the existing user-visible transcription flow rather than requiring users or the frontend to track a separate reduction step.
- **FR-005**: Upload responses MUST remain backward compatible for existing frontend consumers, including the presence of the current processing job reference when processing is created.
- **FR-006**: Session lifecycle states exposed to users MUST remain compatible with the current upload and transcription lifecycle unless a future product decision explicitly changes them.
- **FR-007**: The system MUST preserve the current idempotence and conflict behavior for repeated uploads while audio is already uploaded or processing.
- **FR-008**: The system MUST allow the frontend to poll a single current processing job from upload acceptance through final transcription success or failure.
- **FR-009**: The system MUST remove the raw uploaded recording after audio preparation succeeds.
- **FR-010**: The system MUST retain the prepared audio needed for transcription, playback, retry, deletion, or audit behavior already supported by the session lifecycle.
- **FR-011**: If audio preparation fails, the system MUST record a clear failure outcome that the frontend can display through the existing job or session error flow.
- **FR-012**: If transcription fails after successful preparation, the system MUST keep enough prepared audio state for the user to retry or delete according to existing session rules.
- **FR-013**: Deleting session audio MUST remove raw audio, prepared audio, transcription output, chunks, and derived artifacts associated with the upload.
- **FR-014**: The system MUST document the effective maximum upload size and any deployment limit that can produce a too-large outcome.
- **FR-015**: The feature MUST NOT require any browser-side audio reduction for the normal upload path.

### Key Entities

- **Session Audio Upload**: A raw JDR session recording submitted by the GM; includes size, media type, upload result, and processing ownership by one session.
- **Prepared Audio**: The transcription-ready audio artifact produced from the raw upload; used for transcription and retained according to session lifecycle rules.
- **Processing Job**: The user-visible processing reference that covers audio preparation and transcription from the frontend's perspective.
- **Session**: The JDR session whose state reflects whether audio is uploaded, processing, transcribed, failed, or reset after deletion.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of tested raw JDR audio uploads at or below the documented supported size limit are accepted into processing without browser-side reduction.
- **SC-002**: 100% of tested uploads above the effective supported limit return a clear too-large outcome that includes or allows display of the effective limit.
- **SC-003**: 100% of successful upload flows remain pollable through one user-visible current processing job from upload acceptance through final transcription outcome.
- **SC-004**: 100% of tested successful preparation flows remove the raw uploaded recording while retaining the prepared audio needed by downstream session behavior.
- **SC-005**: 100% of tested preparation failures expose a visible failed outcome and do not present the session as successfully transcribing.
- **SC-006**: Existing frontend upload and polling flows require no new upload entry point, no new user-visible job kind, and no new user-visible session state to complete the same task.

## Assumptions

- The target user is a GM uploading one long JDR session recording at a time.
- The preferred product behavior is one user-visible transcription job that internally covers audio preparation and transcription.
- No new public upload entry point or user-visible response concept is expected for this feature.
- The default storage policy is to delete raw audio after successful preparation and retain only the prepared audio needed by the existing session lifecycle.
- If the deployed stack cannot support 500 MB raw uploads, the lower effective limit will be documented and surfaced to the frontend.
- Browser-side audio reduction remains disabled and is not part of the normal supported path.
