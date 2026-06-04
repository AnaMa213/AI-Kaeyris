# REST API Contract: JDR Job Progress Phase

## Changed Endpoint

`GET /services/jdr/jobs/{job_id}`

### Purpose

Return the existing JDR async job status projection enriched with best-effort transcription progress fields.

### Authentication

Same as existing endpoint:

- Requires an authenticated GM-compatible caller.
- Unknown, expired, malformed, or foreign jobs return the existing not-found behavior.
- Player-role polling remains forbidden.

### Response: `200 OK`

```json
{
  "id": "rq-job-id",
  "kind": "transcription",
  "session_id": "00000000-0000-0000-0000-000000000000",
  "status": "running",
  "failure_reason": null,
  "queued_at": "2026-06-03T10:00:00Z",
  "started_at": "2026-06-03T10:00:02Z",
  "ended_at": null,
  "phase": "transcribing",
  "progress_percent": 42
}
```

### `phase`

Nullable enum:

| Value | Meaning |
|---|---|
| `null` | Progress is unknown, not started, expired, or unavailable. |
| `reducing` | The worker is preparing/reducing the audio before transcription. |
| `transcribing` | The worker is transcribing audio chunks. |
| `done` | The transcription result is persisted and the session is transcribed. |
| `failed` | The transcription job failed after the worker emitted or attempted progress. |

`queued` is intentionally not part of `phase`; use the existing `status` field.

### `progress_percent`

Nullable integer:

| Value | Meaning |
|---|---|
| `null` | Progress is unknown, not started, expired, or unavailable. |
| `0..99` | Known in-flight progress. |
| `100` | Terminal success only, paired with `phase="done"` when metadata is available. |

### Compatibility

Existing clients that ignore unknown JSON fields continue to work. New clients should prefer `phase` and `progress_percent` when non-null, and fall back to the previous estimation behavior when either field is null.

### Error Responses

No new error type is introduced.

| Status | Existing Meaning |
|---|---|
| `401` | Missing/invalid authentication. |
| `403` | Authenticated caller cannot use this job endpoint. |
| `404` | Job does not exist, expired from RQ, is malformed, is not a JDR job, or belongs to another campaign/user scope. |

Missing or expired progress metadata must not produce `500`.

## OpenAPI Requirements

The generated `JobOut` schema must include:

- `phase`: nullable enum containing `reducing`, `transcribing`, `done`, `failed`.
- `progress_percent`: nullable integer with minimum `0` and maximum `100`.

The synced OpenAPI artifact at `docs/context/api/openapi.json` must be regenerated before frontend type generation.
