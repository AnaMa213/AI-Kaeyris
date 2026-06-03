# Research: Current Job and Audio Stream

## Decision: Store the current transcription job pointer on Session

**Decision**: Add an optional `current_job_id` pointer to the session entity and expose it on session outputs.

**Rationale**: The frontend must reconstruct transcription polling after refresh from server state alone. Keeping the most recent transcription job pointer on the session makes the session detail response sufficient for this flow. The field is additive and nullable, so existing session consumers remain compatible.

**Alternatives considered**:

- Keep the job id only in upload response: rejected because browser refresh loses local state.
- Derive the latest job by querying all jobs for a session: rejected for this story because it expands scope into a broader job listing API.
- Replace the pointer with a multi-job dictionary: rejected as premature for Epic 4 and contrary to the current transcription-only need.

## Decision: Do not clear the job pointer at terminal transcription states

**Decision**: Leave `current_job_id` set after transcription success or failure until audio is purged or the referenced job is no longer valid.

**Rationale**: The session lifecycle state remains the source of truth for terminal UI state, while the job pointer lets the UI inspect final job status and failure context. Clearing on success or failure would recreate the refresh gap BD-8 is meant to remove.

**Alternatives considered**:

- Clear on success: rejected because the UI may still need to inspect the final job result after refresh.
- Clear on failure: rejected because the frontend needs failure context for the failed-job UI.

## Decision: Preserve source audio after transcription success and failure

**Decision**: Keep the source audio available after both successful and failed transcription until the GM explicitly deletes it.

**Rationale**: Story 3.5 needs a player for already processed sessions, and Story 3.6 needs a deliberate replacement flow. The current auto-purge-after-success behavior conflicts with playback and transcribed-session replacement. Failure must also keep audio so retry can occur without requiring a re-upload.

**Alternatives considered**:

- Keep auto-purge after success: rejected because a transcribed session would not be playable.
- Purge on failure: rejected because retry would force re-upload and hide useful failure context.

## Decision: Stream audio directly with byte-range support

**Decision**: Expose direct authenticated audio retrieval with byte-range support for browser playback and seeking.

**Rationale**: The frontend handoff explicitly prefers a direct stream so the browser can use the existing authentication context and avoid a preliminary signed-URL round trip. Byte ranges are required for reliable seek/scrub behavior in browser audio players.

**Alternatives considered**:

- Short-lived signed URL: acceptable future option for object storage, but rejected now because current local storage can serve the file directly and the frontend prefers one request.
- Full-file download only: rejected because seek controls need partial retrieval.

## Decision: Make audio deletion irreversible and idempotent except while transcribing

**Decision**: Deleting audio permanently removes source audio, transcription, chunks, derived artifacts, clears `current_job_id`, and resets the session to `created`. Deletion is allowed for `created`, `audio_uploaded`, `transcription_failed`, and `transcribed`; it is refused for `transcribing`.

**Rationale**: The product decision is that replacement is destructive. Idempotent delete on `created` reduces frontend branching and makes repeated confirmation calls safe. Active transcription remains blocked because deleting while a worker may use the file would require clean job cancellation, which is out of scope.

**Alternatives considered**:

- Keep the existing 404 for no audio: rejected because BD-8 prefers idempotent 204 for already-created sessions.
- Allow delete while transcribing: rejected because safe worker cancellation is not part of this story.
- Soft-delete audio/transcription: rejected because the product decision is irreversible replacement.

## Decision: Keep changes inside JDR service boundaries

**Decision**: Implement model, schema, repository, route, job, and tests within existing JDR service modules and existing storage conventions.

**Rationale**: This follows the modular monolith structure and avoids introducing a cross-service abstraction for a feature-specific concern.

**Alternatives considered**:

- Introduce a new audio service module outside JDR: rejected because this is JDR-session-specific behavior.
- Add a generic media service: rejected as YAGNI for the current milestone.
