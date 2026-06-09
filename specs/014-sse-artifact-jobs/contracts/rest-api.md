# REST API Contract: Live Job Events

## New Endpoint

`GET /services/jdr/jobs/{job_id}/events`

### Purpose

Stream live progress updates for an existing JDR async job. The endpoint is generic across transcription and artifact jobs.

### Authentication

Same visibility rules as `GET /services/jdr/jobs/{job_id}`:

- Requires authenticated GM credentials.
- Player credentials are rejected.
- Unknown, expired, malformed, non-JDR, or foreign jobs return the existing not-found behavior.

### Success Response: `200 OK`

Content type:

```http
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache
```

Each event uses:

```text
event: progress
data: {"status":"running","phase":null,"progress_percent":null}

```

### Event Payload

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `status` | string | yes | Public job status: `queued`, `running`, `succeeded`, `failed`. |
| `phase` | string/null | yes | Same value as `JobOut.phase`; usually null for artifact jobs. |
| `progress_percent` | integer/null | yes | Same value as `JobOut.progress_percent`; usually null for artifact jobs. |
| `failure_reason` | string/null | terminal failed only | Include when the job failed and a reason is available. |

The payload intentionally mirrors the status subset of `JobOut`. The endpoint may internally read other `JobOut` fields, but streamed events should stay compact.

### Running Artifact Example

```text
event: progress
data: {"status":"running","phase":null,"progress_percent":null}

```

### Terminal Success Example

```text
event: progress
data: {"status":"succeeded","phase":null,"progress_percent":null}

```

The stream closes after this frame.

### Terminal Failure Example

```text
event: progress
data: {"status":"failed","phase":null,"progress_percent":null,"failure_reason":"app.jobs.TransientJobError: APIConnectionError: Connection error."}

```

The stream closes after this frame.

### Job Unavailable After Subscription Example

If the RQ job is deleted or expires after the stream has already opened, the
HTTP status cannot change anymore. The stream sends one final progress frame and
then closes:

```text
event: progress
data: {"status":"failed","phase":null,"progress_percent":null,"failure_reason":"Job is no longer available."}

```

### Transcription Progress Example

```text
event: progress
data: {"status":"running","phase":"transcribing","progress_percent":42}

```

### Error Responses

Same categories as the existing polling route:

| Status | Meaning |
|--------|---------|
| `401` | Missing or invalid authentication. |
| `403` | Authenticated caller cannot use this GM job endpoint. |
| `404` | Job does not exist, expired, is malformed, is not a recognized JDR job, or belongs to another scope. |

### OpenAPI Requirements

- `GET /services/jdr/jobs/{job_id}/events` is present in `docs/context/api/openapi.json`.
- The successful response documents media type `text/event-stream`.
- The route description documents `event: progress` and the JSON `data` fields.

## Existing Endpoint Compatibility

`GET /services/jdr/jobs/{job_id}` remains unchanged and continues to return the full `JobOut` JSON projection. Existing polling clients remain supported.
