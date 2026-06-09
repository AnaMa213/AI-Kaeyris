# REST API Contract: BD-11 LLM Connectivity

No new endpoint is introduced. Existing frontend routes remain stable.

## POST `/services/jdr/sessions/{session_id}/artifacts/summary`

Queues non-diarised summary generation for a transcribed JDR session.

### Preconditions

- Caller is authenticated as the GM who owns the session.
- Session exists.
- Session transcription mode is `non_diarised`.
- Session state is `transcribed`.
- Session has at least one stored transcription chunk.

### Success Response: `202 Accepted`

```json
{
  "id": "rq-job-id",
  "kind": "summary",
  "session_id": "00000000-0000-0000-0000-000000000000",
  "status": "queued",
  "queued_at": "2026-06-09T12:00:00Z"
}
```

### Contract Notes

- The endpoint does not synchronously call the LLM.
- LLM connectivity errors surface through the job polling endpoint.
- Response shape remains compatible with the existing frontend.

## GET `/services/jdr/jobs/{job_id}`

Returns the current public projection of an async JDR job.

### Success Response While Queued/Running

```json
{
  "id": "rq-job-id",
  "kind": "summary",
  "session_id": "00000000-0000-0000-0000-000000000000",
  "status": "queued",
  "failure_reason": null,
  "queued_at": "2026-06-09T12:00:00Z",
  "started_at": null,
  "ended_at": null,
  "phase": null,
  "progress_percent": null
}
```

### Success Response After LLM Failure Exhaustion

```json
{
  "id": "rq-job-id",
  "kind": "summary",
  "session_id": "00000000-0000-0000-0000-000000000000",
  "status": "failed",
  "failure_reason": "TransientJobError: APIConnectionError: Connection error.",
  "queued_at": "2026-06-09T12:00:00Z",
  "started_at": "2026-06-09T12:00:05Z",
  "ended_at": "2026-06-09T12:00:08Z",
  "phase": null,
  "progress_percent": null
}
```

### Required Failure Semantics

- `failure_reason` is non-empty when `status` is `failed` for an exhausted LLM
  connectivity/unavailability failure.
- The reason is concise and suitable for display/logging.
- Cross-tenant access still returns 404.

## GET `/services/jdr/sessions/{session_id}/artifacts/summary`

Returns the completed summary artifact.

### Success Response: `200 OK`

```json
{
  "session_id": "00000000-0000-0000-0000-000000000000",
  "text": "Session summary text...",
  "model_used": "provider:model",
  "generated_at": "2026-06-09T12:05:00Z"
}
```

### Contract Notes

- Behavior is unchanged.
- Failed LLM jobs do not create a summary artifact.
