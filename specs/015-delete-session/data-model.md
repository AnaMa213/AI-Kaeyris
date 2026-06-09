# Data Model: Delete JDR Session

## Existing Entities

### Session

Central aggregate being deleted.

Relevant fields:

- `id`
- `gm_key_id`
- `campaign_id`
- `state`
- `current_job_id`
- `edited_transcript_md`

Deletion rules:

- Only the current GM can delete a visible session.
- Unknown or foreign sessions are treated as not found.
- `state=transcribing` blocks deletion.
- A current RQ job blocks deletion only while its observable status is active.
- Once deleted, the session must not appear in direct reads or list reads.

### Session Dependencies

Owned data attached to a session.

Existing owned resources:

- Audio source metadata and stored audio file.
- Diarised transcription.
- Non-diarised chunks.
- Speaker-to-PJ mapping.
- Session player presence.
- Generated artifacts: summary, narrative, elements, POVs.
- Job projection rows associated with the session.
- Manual transcript override stored on the session row.

Deletion rules:

- Database-owned dependencies are removed with the session.
- Stored audio file and raw upload temp directory are removed best-effort.
- Missing audio files do not block deletion.
- Player character rows are not deleted; only session-scoped relations are removed.

### Active Work

Represents transcription or artifact generation that may still write to a session.

Signals:

- Session state indicates transcription in progress.
- Current job marker points at an RQ job whose observable status is active (`queued`, `deferred`, `scheduled`, or `started`).

Deletion rules:

- Active work returns a conflict and leaves the session intact.
- Terminal, missing, or expired job metadata does not by itself block deletion if the session is otherwise deletable.

### Campaign Summary

Read model exposed to campaign users.

Deletion rules:

- Session count reflects the remaining visible sessions after deletion.
- No stored counter is introduced.

## State Transitions

```text
created/audio_uploaded/transcription_failed/transcribed
  -> delete request
  -> deleted aggregate

transcribing or observable active current RQ job
  -> delete request
  -> conflict, unchanged session
```

## Validation Rules

- GM authentication is required.
- Campaign scope is applied the same way as existing session reads.
- Foreign sessions return not found rather than forbidden.
- Deletion is not idempotent for already-deleted sessions: a later request returns not found.
