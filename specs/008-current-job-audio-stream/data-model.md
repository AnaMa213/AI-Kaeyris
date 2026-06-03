# Data Model: Current Job and Audio Stream

## Session

Represents one JDR session and remains the central aggregate for transcription lifecycle.

### Fields Added or Changed

- `current_job_id`: optional identifier of the current or most recent transcription job for the session.

### Relationships

- `current_job_id` points to a transcription job when one exists.
- The pointer is optional and may be empty when no job exists, the audio has been purged, or the referenced job has been removed.
- Existing relationships to audio source, transcription, chunks, artifacts, mappings, and players remain unchanged.

### Validation and Invariants

- `current_job_id` is set when audio upload creates a transcription job.
- `current_job_id` is not cleared when the job succeeds.
- `current_job_id` is not cleared when the job fails.
- `current_job_id` is cleared when the audio is destructively purged.
- Session state, not the presence of `current_job_id`, determines whether the session is terminal, failed, or still processing.

### State Transitions

| Event | From State | To State | Job Pointer | Audio |
|-------|------------|----------|-------------|-------|
| Upload audio | `created` | `audio_uploaded` | Set to new transcription job | Stored |
| Worker starts | `audio_uploaded` | `transcribing` | Unchanged | Stored |
| Worker succeeds | `transcribing` | `transcribed` | Unchanged | Stored |
| Worker fails permanently | `transcribing` | `transcription_failed` | Unchanged | Stored |
| Delete audio | `created` | `created` | Cleared | Absent |
| Delete audio | `audio_uploaded` | `created` | Cleared | Purged |
| Delete audio | `transcription_failed` | `created` | Cleared | Purged |
| Delete audio | `transcribed` | `created` | Cleared | Purged |
| Delete audio | `transcribing` | `transcribing` | Unchanged | Stored |

## Transcription Job

Represents an asynchronous transcription pipeline job.

### Fields Used

- `id`: stable job identifier exposed through `Session.current_job_id`.
- `kind`: must identify transcription jobs when used as a session current job.
- `status`: queued, running, succeeded, or failed.
- `failure_reason`: available for failed jobs when supported by the existing job projection.

### Validation and Invariants

- Only transcription pipeline jobs should be assigned to `current_job_id`.
- A session may have historical jobs, but `current_job_id` points to the active or most recent transcription pipeline job.

## Session Audio

Represents the source audio uploaded for a session.

### Fields Used

- `session_id`: owning session.
- `path`: relative path to stored audio.
- `sha256`: integrity checksum.
- `size_bytes`: full audio size.
- `duration_seconds`: optional detected duration.
- `uploaded_at`: upload timestamp.
- `purged_at`: set only after explicit destructive deletion.

### Validation and Invariants

- Successful transcription no longer marks the audio as purged.
- Failed transcription no longer marks the audio as purged.
- Audio retrieval only serves audio with no `purged_at` value and a readable file.
- Destructive deletion marks the audio purged and removes the file best-effort.

## Derived Audio Artifacts

Represents all data generated from a specific audio source.

### Included Data

- Diarised transcription rows.
- Non-diarised chunks and chunk summaries.
- Narrative, elements, summary, and POV artifacts derived from the audio/transcription.

### Validation and Invariants

- Destructive audio deletion removes or invalidates all derived data tied to the previous audio.
- After destructive deletion, the session behaves like a fresh `created` session and can accept a new audio upload.
