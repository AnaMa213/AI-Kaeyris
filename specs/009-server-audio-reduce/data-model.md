# Data Model: Server Audio Reduce

## Existing Entities Touched

### Session

Represents a JDR session and remains the lifecycle source of truth.

**Relevant fields**:

- `id`: session identifier.
- `state`: existing lifecycle state.
- `current_job_id`: optional pointer to the current or most recent transcription processing job.
- `transcription_mode`: existing mode that decides whether final text is stored as diarised segments or non-diarised chunks.

**State transitions for this feature**:

```text
created
  -> audio_uploaded       after accepted raw upload
  -> transcribing         when the worker starts preparation/transcription
  -> transcribed          when transcription output is persisted
  -> transcription_failed when preparation or transcription fails permanently

audio_uploaded/transcribed/transcription_failed
  -> created              after explicit audio delete/reset
```

No `reducing` state is introduced.

### AudioSource

Existing session-owned audio metadata row.

**Current role**: Tracks the audio file associated with a session.

**BD-9 role**: Tracks the retained audio artifact after preparation. The row may initially point at the raw upload, then be updated to point at the prepared audio once preparation succeeds.

**Relevant fields**:

- `session_id`: one-to-one session owner.
- `path`: relative path under `KAEYRIS_DATA_DIR`.
- `sha256`: checksum of the file represented by `path`.
- `size_bytes`: file size of the file represented by `path`.
- `duration_seconds`: best-effort duration; may remain null.
- `uploaded_at`: original upload time.
- `purged_at`: set only by explicit deletion/reset.

**Validation rules**:

- There is at most one active audio source per session.
- `path` must stay relative to configured data storage.
- `purged_at != null` means the audio is not serveable or reusable.

### Job

Existing lightweight processing projection for queue jobs.

**Relevant fields**:

- `id`: queue job identifier.
- `kind`: remains `transcription` for this feature.
- `session_id`: owner session.
- `status`: queued, running, succeeded, or failed.
- `failure_reason`: human-readable operational failure context.
- `queued_at`, `started_at`, `ended_at`: lifecycle timestamps.

**Validation rules**:

- Audio preparation is represented inside the same `transcription` job.
- No `audio_reduce` kind is required for BD-9.

## New Domain Concept

### Prepared Audio

Prepared Audio is a durable file produced from the raw upload before transcription.

**Persistence shape**:

- No separate table is required for the first implementation.
- After successful preparation, `AudioSource.path`, `sha256`, and `size_bytes` should represent the prepared file.
- Raw upload can live temporarily under a worker-managed path until preparation succeeds.

**Lifecycle**:

```text
raw upload stored
  -> prepared audio produced
  -> AudioSource points to prepared audio
  -> raw upload deleted
  -> prepared audio retained until explicit delete
```

**Failure behavior**:

- If preparation fails before a prepared file exists, keep enough state to diagnose failure and prevent a false success.
- If cleanup of raw fails after prepared audio is committed, log it and keep the session's canonical audio pointer on the prepared file.

## File Layout Proposal

The exact names can be adjusted during implementation, but the plan assumes two clearly separated paths:

```text
KAEYRIS_DATA_DIR/
|-- audios/
|   `-- <session_id>.m4a              # retained prepared audio after success
`-- .tmp/
    `-- audio-reduce/
        `-- <session_id>/
            `-- raw.m4a               # transient raw upload/preparation input
```

This keeps retained audio under the existing audio directory while making transient raw files easy to clean with a janitor later if needed.

## Invariants

- Business code in `app/services/jdr` must not reference a vendor provider by name.
- A successful preparation must not leave `AudioSource` pointing to a deleted raw file.
- A successful preparation must leave one retained audio artifact for playback, retry, and explicit delete semantics.
- Explicit delete must remove raw leftovers, prepared audio, transcription rows, chunks, artifacts, and `current_job_id`.
- A permanent preparation failure must not leave the session in `transcribing`.
- Re-upload while an audio source exists remains a conflict.

## Migration Impact

No database migration is required for the default design. The existing `AudioSource` row can represent the current canonical audio file.

If implementation discovers a real need to audit both raw and prepared metadata at the same time, that is a scope expansion and should go through a new decision before adding columns or a table.
