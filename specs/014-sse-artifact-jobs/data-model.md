# Data Model: Live Job Events

BD-14 does not add database tables or columns. It defines a live transport projection over existing job state.

## Entity: JDR Job

Existing asynchronous job associated with one JDR session.

### Existing Relevant Fields

| Field | Meaning |
|-------|---------|
| `id` | Queue job identifier. |
| `kind` | Public JDR job kind: transcription, summary, narrative, elements, or POVs. |
| `session_id` | Session used for authorization and job context. |
| `status` | Authoritative lifecycle status: queued, running, succeeded, failed. |
| `failure_reason` | Optional short failure detail for failed jobs. |
| `queued_at` | Time the job entered the queue. |
| `started_at` | Time execution started, if known. |
| `ended_at` | Time execution ended, if known. |
| `phase` | Optional progress phase from BD-10. |
| `progress_percent` | Optional progress percentage from BD-10. |

### Validation Rules

- Unknown, expired, malformed, or foreign jobs are not visible.
- `status` remains the completion source of truth.
- `phase` remains nullable and limited to the existing BD-10 vocabulary.
- `progress_percent` remains nullable and otherwise bounded from 0 to 100.
- Artifact jobs are valid when `phase` and `progress_percent` are both null.

## Entity: Live Job Event

One streamed event sent to a subscribed client.

### Fields

| Field | Required | Meaning |
|-------|----------|---------|
| `status` | yes | Current public job lifecycle status. |
| `phase` | yes, nullable | Current phase when known. |
| `progress_percent` | yes, nullable | Current progress percentage when known. |
| `failure_reason` | no | Included for failed terminal events when available. |

### Event Envelope

```text
event: progress
data: {"status":"running","phase":null,"progress_percent":null}

```

### Terminal Event Rules

```text
queued/running
  -> emit progress event
  -> wait
  -> re-read job

succeeded
  -> emit final progress event
  -> close stream

failed
  -> emit final progress event with failure_reason when available
  -> close stream
```

## Entity: Polling Fallback

Existing single-job status read.

### Relationship To Live Events

- Polling and live events use the same authorization boundary.
- Polling and live events project from the same job source.
- Live events do not replace polling; they are an optional comfort channel.

## Persistence Impact

- No migration.
- No new persisted state.
- No new durable event history.
- Existing RQ metadata and SQL job/session projections remain the state sources.
