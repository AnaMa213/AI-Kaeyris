# Quickstart: Current Job and Audio Stream

## Preconditions

- The API test environment is configured.
- Redis/RQ test doubles or existing test helpers can enqueue transcription jobs.
- The current branch is `codex/008-current-job-audio-stream`.

## Development Flow

1. Add a nullable current transcription job pointer to the session model and migration.
2. Expose the pointer on session output schemas and all session endpoints.
3. Set the pointer when audio upload enqueues the transcription job.
4. Preserve source audio after successful and failed transcription.
5. Add authenticated audio retrieval with full-file and byte-range responses.
6. Update destructive audio deletion to clear job pointer, remove derived data, reset state, and allow idempotent delete outside active transcription.
7. Update tests for upload, session detail/list, transcription flow, audio retrieval, purge, and cross-campaign access.

## Manual Verification

### Resume Polling State

1. Create a campaign and a session.
2. Upload audio to the session.
3. Fetch the session detail.
4. Verify `current_job_id` is present and equals the upload response `job_id`.
5. Complete or fail the transcription job.
6. Fetch the session detail again.
7. Verify `current_job_id` is still present.

### Play Audio

1. Upload audio to a session.
2. Request `GET /services/jdr/sessions/{session_id}/audio`.
3. Verify the response is playable audio and includes `Accept-Ranges: bytes`.
4. Request a valid byte range.
5. Verify the response is `206` with `Content-Range`.

### Delete Audio

1. Delete audio from a session in `audio_uploaded`, `transcription_failed`, and `transcribed`.
2. Verify each returns 204 and the session returns to `created`.
3. Delete again from `created`.
4. Verify it still returns 204.
5. Attempt delete while `transcribing`.
6. Verify it returns 409 and preserves the session state.

## Quality Gates

Run:

```powershell
$env:UV_CACHE_DIR='D:\Projets\dev\AI-Kaeyris\.uv-cache'
uv run ruff check .
uv run pytest tests\services\jdr -q
uv run pytest tests\jobs -q
```
