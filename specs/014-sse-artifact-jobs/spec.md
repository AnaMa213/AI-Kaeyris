# Feature Specification: Live Job Events

**Feature Branch**: `codex/014-sse-artifact-jobs`
**Created**: 2026-06-09
**Status**: Draft
**Input**: Backend handoff BD-14 asks to deliver the optional live job event capability left open in BD-10, generalized to all JDR jobs. The current polling read remains available, but the frontend should be able to receive pushed status updates for artifact jobs and transcription jobs, including terminal success or failure.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Follow Artifact Jobs Live (Priority: P1)

As a GM who starts an artifact generation, I want the job status to update live so that I can see when the generated summary, narrative, elements, or POVs are ready without waiting for the next polling interval.

**Why this priority**: Artifact generation already works through polling, but the user experience still feels delayed. The core value of BD-14 is removing that visible delay for artifact jobs.

**Independent Test**: Can be tested by starting from a running artifact job, subscribing to its live updates, changing the job to a terminal state, and confirming the subscriber receives the current status and terminal status before the live stream ends.

**Acceptance Scenarios**:

1. **Given** a GM is allowed to view a running artifact job, **When** the GM subscribes to live job updates, **Then** the GM receives progress updates containing the current job status.
2. **Given** a subscribed artifact job completes successfully, **When** the success state is recorded, **Then** the GM receives a final successful update and the live update channel closes.
3. **Given** a subscribed artifact job fails, **When** the failure state is recorded, **Then** the GM receives a final failed update including the failure reason when one is available, and the live update channel closes.

---

### User Story 2 - Use One Live Tracking Model For All Jobs (Priority: P2)

As a frontend maintainer, I want the same live tracking behavior to work for transcription jobs and artifact jobs so that the UI can use one job tracking model instead of special cases per job type.

**Why this priority**: BD-14 explicitly generalizes the BD-10 live update idea. A generic capability prevents duplicated frontend logic and avoids a separate route or behavior per artifact kind.

**Independent Test**: Can be tested by subscribing to one artifact job and one transcription job with the same client behavior, then confirming both produce compatible update payloads.

**Acceptance Scenarios**:

1. **Given** a GM can view a transcription job, **When** the GM subscribes to live job updates, **Then** the update payload uses the same fields as artifact job updates.
2. **Given** a transcription job has phase or percent information, **When** an update is sent, **Then** those progress fields are included when known.
3. **Given** an artifact job has no phase or percent information, **When** an update is sent, **Then** the status is still reported and the missing progress fields remain empty.

---

### User Story 3 - Preserve Polling Fallback And Visibility Rules (Priority: P3)

As a GM or frontend maintainer, I need the existing job status read to keep working so that clients can fall back to polling when live updates are unavailable, and unauthorized users still cannot observe another GM's jobs.

**Why this priority**: Live updates are a comfort feature. They must not weaken isolation or break the already functional fallback.

**Independent Test**: Can be tested by checking that the existing job status read still returns the same payload for known jobs, and that live update attempts for unknown or unauthorized jobs are rejected consistently with normal job reads.

**Acceptance Scenarios**:

1. **Given** a client cannot use live updates, **When** it reads a job through the existing status read, **Then** it still receives the current job status.
2. **Given** a GM attempts to subscribe to a job they cannot view, **When** the subscription is requested, **Then** the job remains hidden or unavailable.
3. **Given** a requested job no longer exists or has expired, **When** live updates are requested, **Then** the client receives a clear not-found outcome rather than an open stream with no useful data.

### Edge Cases

- A job reaches a terminal state before the client subscribes.
- A job changes status while the client is already subscribed.
- A job fails and has a recorded failure reason.
- An artifact job has no granular phase or percent information.
- A transcription job has granular phase or percent information.
- A job is unknown, expired, or belongs to another GM.
- The live connection is unavailable or interrupted and the client falls back to the existing status read.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST allow authorized clients to subscribe to live status updates for a visible JDR job.
- **FR-002**: The live update payload MUST expose the same job status fields already used by the existing job status read.
- **FR-003**: The system MUST send live updates for artifact jobs, including summary, narrative, elements, and POV generation jobs.
- **FR-004**: The system MUST send live updates for transcription jobs through the same live tracking capability.
- **FR-005**: For artifact jobs, the system MUST report the current status even when granular phase and percent information are not available.
- **FR-006**: For transcription jobs, the system MUST include phase and percent information when that information is available.
- **FR-007**: The system MUST send one final update when a job succeeds, then close the live update channel.
- **FR-008**: The system MUST send one final update when a job fails, include the failure reason when available, then close the live update channel.
- **FR-009**: The existing single-job status read MUST continue to work as a fallback and MUST keep its current response semantics.
- **FR-010**: Live update access MUST preserve the same job visibility and ownership rules as the existing job status read.
- **FR-011**: Requests for unknown, expired, or unauthorized jobs MUST not expose job details to unauthorized users.
- **FR-012**: The frontend contract documentation MUST make the live job update capability discoverable and describe the event payload shape.
- **FR-013**: BD-14 MUST NOT require data migration or new persisted job state.

### Key Entities

- **JDR Job**: Existing asynchronous work item associated with a JDR session, such as transcription, summary, narrative, elements, or POV generation.
- **Job Status Update**: A live projection of a job's current state, including status and optional progress details.
- **Terminal Job Update**: The final live update sent when a job reaches success or failure.
- **GM**: The authorized user who can observe jobs for sessions they are allowed to access.
- **Polling Fallback**: Existing job status read used when live updates are unavailable or intentionally not used.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For tested artifact jobs, clients receive a terminal live update within 2 seconds of the job reaching success or failure.
- **SC-002**: 100% of supported job categories already visible through the job status read can be tracked through the live update capability.
- **SC-003**: 100% of tested failed jobs with a recorded failure reason expose that reason in the terminal live update.
- **SC-004**: 100% of existing job status polling tests continue to pass without response contract changes.
- **SC-005**: 100% of tested unauthorized or unknown live update requests avoid exposing another GM's job details.
- **SC-006**: Frontend contract generation can discover the live job update capability and its payload fields.

## Assumptions

- BD-14 is a comfort improvement; existing polling remains the functional fallback.
- Existing job status and visibility rules remain the source of truth.
- Artifact jobs currently expose status only; empty phase and percent values are acceptable for them.
- Transcription jobs may expose phase and percent when already recorded by the existing progress mechanism.
- Live updates are scoped to JDR jobs already tracked by the current job status surface.
- No new persisted job lifecycle state is needed for this feature.
