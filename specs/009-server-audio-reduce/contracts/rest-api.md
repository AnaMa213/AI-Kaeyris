# REST Contract: Server Audio Reduce

BD-9 keeps the public upload and polling contract stable. This document records expected behavior changes and non-breaking clarifications.

## POST /services/jdr/sessions/{session_id}/audio

Uploads raw JDR session audio and enqueues processing.

### Request

- Authentication: GM required.
- Path parameter: `session_id`.
- Body: multipart form with field `audio`.
- Supported media types remain M4A-compatible audio uploads.
- The frontend no longer needs to reduce the audio before upload.

### Success Response: 202

Response body remains `AudioUploadOut`.

Required behavior:

- `job_id` is populated.
- The job is user-visible as `transcription`.
- `duration_seconds` may be null.
- The session can be polled through the existing session/job flow.

Example shape:

```json
{
  "session_id": "00000000-0000-0000-0000-000000000000",
  "path": "audios/00000000-0000-0000-0000-000000000000.m4a",
  "sha256": "64-hex-character-checksum",
  "size_bytes": 123456789,
  "duration_seconds": null,
  "uploaded_at": "2026-06-03T12:00:00Z",
  "job_id": "rq-job-id"
}
```

### Error Responses

- `401`: missing or invalid authentication.
- `404`: session absent or outside the caller's campaign scope.
- `409`: audio already uploaded or processing for the session.
- `413`: upload exceeds the effective server-side upload limit.
- `415`: unsupported audio media type.

### 413 Problem Details

The too-large response should allow the frontend to display the effective limit.

Example shape:

```json
{
  "type": "https://errors.ai-kaeyris.local/audio-upload-too-large",
  "title": "Audio upload too large",
  "status": 413,
  "detail": "Audio upload exceeds the 500 MB limit.",
  "instance": "/services/jdr/sessions/00000000-0000-0000-0000-000000000000/audio",
  "limit_bytes": 524288000
}
```

If the shared Problem Details helper does not support extension fields cleanly, include the limit in `detail` and document the exact text contract for the frontend.

## GET /services/jdr/jobs/{job_id}

Existing polling endpoint.

Required behavior for BD-9:

- The upload-created job remains `kind = "transcription"`.
- Preparation and transcription failures both surface as a failed transcription processing job.
- `failure_reason` should distinguish preparation failure from transcription provider failure in human-readable form.

No new job kind is introduced.

## GET /services/jdr/sessions/{session_id}

Existing session detail endpoint.

Required behavior for BD-9:

- `current_job_id` continues to point to the single processing job after upload.
- No `reducing` session state is exposed.
- Existing states remain compatible: `audio_uploaded`, `transcribing`, `transcribed`, `transcription_failed`.

## GET /services/jdr/sessions/{session_id}/audio

Existing BD-8 audio retrieval endpoint.

Required behavior for BD-9:

- After successful preparation, this endpoint serves the retained prepared audio, not the deleted raw upload.
- Range behavior remains unchanged.
- If preparation failed before a prepared file exists, audio retrieval should follow the existing not-found or unavailable behavior chosen by the implementation.

## DELETE /services/jdr/sessions/{session_id}/audio

Existing destructive reset endpoint.

Required behavior for BD-9:

- Delete raw leftovers if present.
- Delete prepared audio if present.
- Delete transcription rows, chunks, and generated artifacts tied to the upload.
- Clear `current_job_id`.
- Reset the session to `created` when allowed.
- Keep the existing conflict behavior while active transcription processing is running.

## Backward Compatibility

- No new route is required.
- No new user-visible job kind is required.
- No new user-visible session state is required.
- Existing frontend upload and polling flows should continue to work.
