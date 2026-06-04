# Data Model: JDR Job Progress Phase

## Entity: JDR Job Status Projection

Represents the public job detail returned by `GET /services/jdr/jobs/{job_id}`.

### Fields

| Field | Type | Required | Rules |
|---|---|---:|---|
| `id` | string | yes | Existing RQ job identifier. |
| `kind` | enum | yes | Existing `transcription`, `narrative`, `elements`, `povs`, or `summary`. |
| `session_id` | UUID | yes | Existing associated JDR session id. |
| `status` | enum | yes | Existing `queued`, `running`, `succeeded`, or `failed`; remains source of truth for lifecycle. |
| `failure_reason` | string/null | no | Existing last-line failure projection. |
| `queued_at` | datetime | yes | Existing timestamp. |
| `started_at` | datetime/null | no | Existing timestamp. |
| `ended_at` | datetime/null | no | Existing timestamp. |
| `phase` | enum/null | no | New. One of `reducing`, `transcribing`, `done`, `failed`, or `null` when unknown/not started/expired. |
| `progress_percent` | integer/null | no | New. Integer from 0 to 100, or `null` when unknown/not started/expired. |

### Validation Rules

- `phase` must be nullable and otherwise one of the four closed values.
- `progress_percent` must be nullable and otherwise `0 <= value <= 100`.
- `status` must stay authoritative for job lifecycle.
- `phase="done"` implies `progress_percent=100` when progress metadata is available.
- A running transcription job should not expose `progress_percent=100`.

## Entity: Progress Metadata

Temporary worker progress attached to a running RQ job.

### Fields

| Field | Type | Required | Rules |
|---|---|---:|---|
| `phase` | string | no | Same domain as public `phase`; absent until the worker emits progress. |
| `progress_percent` | integer | no | Same domain as public `progress_percent`; absent until the worker emits progress. |

### Lifecycle

```text
queued job
  -> metadata absent

worker starts optional reduction
  -> phase=reducing, progress_percent=0

worker starts transcription
  -> phase=transcribing, progress_percent=0

each completed chunk
  -> phase=transcribing, progress_percent=min(99, round(done / total * 100))

successful persistence and session state transition
  -> phase=done, progress_percent=100

permanent or transient failure after progress exists
  -> phase=failed, progress_percent=last known value
```

### Invariants

- Missing metadata is valid and maps to public `null` fields.
- Metadata expiration must not affect the public job's main status projection.
- Progress should be monotone for one job execution.
- `queued` is not a phase; it is already represented by `status`.

## Entity: Chunk Progress Event

Internal callback event emitted by chunked transcription.

### Fields

| Field | Type | Required | Rules |
|---|---|---:|---|
| `chunks_done` | integer | yes | Number of completed chunks; starts at 1 after the first chunk. |
| `chunks_total` | integer | yes | Total chunks discovered for the audio; must be positive when callback is called. |

### Validation Rules

- `1 <= chunks_done <= chunks_total`.
- Events are emitted after successful transcription of each chunk.
- The callback is optional; no-callback execution preserves existing transcription behavior.

## Persistence

No database schema change is planned. `jdr_jobs` remains the durable projection for lifecycle fields, while progress metadata is transient and best-effort.
