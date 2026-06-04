# Research: JDR Job Progress Phase

## Decision 1: Use RQ job metadata for v1 progress

**Decision**: Store `phase` and `progress_percent` on the currently running RQ job metadata, then have `GET /services/jdr/jobs/{job_id}` read the same metadata when projecting `JobOut`.

**Rationale**: RQ officially supports custom job metadata through `job.meta` plus `job.save_meta()`, and a job function can access its current job with `get_current_job()` while code outside a job context receives `None`. RQ also supports refreshed metadata reads with `get_meta(refresh=True)`. Source: https://python-rq.org/docs/jobs/

**Alternatives considered**:

- **Database columns on `jdr_jobs`**: rejected for v1 because progress is temporary UX state and would create a write per chunk. Existing SQL job/session status remains the durable source of completion.
- **Redis pub/sub**: rejected because it adds a second eventing contract while the current frontend already polls job status.
- **SSE endpoint**: deferred because it would still need to bridge worker process state to the web process; for batch transcription, enriched polling is enough unless measured latency proves otherwise.

## Decision 2: Keep progress fields nullable

**Decision**: `phase` and `progress_percent` are nullable on `JobOut`.

**Rationale**: Existing jobs, queued jobs, expired RQ metadata, and Redis metadata read failures must not break the job-detail contract. The primary job status already communicates `queued`, `running`, `succeeded`, and `failed`.

**Alternatives considered**:

- **Default phase to `queued`**: rejected because it duplicates `JobStatus.QUEUED` and creates two sources of truth.
- **Default progress to `0`**: rejected because it would make "unknown progress" indistinguishable from a real initial progress event.

## Decision 3: Use a closed phase vocabulary

**Decision**: `phase` is limited to `reducing`, `transcribing`, `done`, and `failed`.

**Rationale**: The frontend needs stable typed values and localized display labels. A closed vocabulary avoids free-form phase strings leaking worker internals into the public contract.

**Alternatives considered**:

- **Free-form string**: rejected because it weakens OpenAPI/TypeScript generation and makes regressions harder to detect.
- **Expose French labels directly**: rejected because display language belongs to the frontend; the backend should expose stable semantic values.

## Decision 4: Add a queue-agnostic progress callback to chunked transcription

**Decision**: `_transcribe_with_optional_chunking` accepts an optional callback shaped around `(chunks_done, chunks_total)`. The RQ entry path maps callback events to job metadata.

**Rationale**: The chunk helper already owns the denominator for real progress, but it should remain testable without Redis/RQ. A callback exposes progress while keeping queue-specific behavior at the job boundary.

**Alternatives considered**:

- **Pass the RQ job object into the chunk helper**: rejected because it couples transcription logic to RQ and makes direct async tests depend on Redis context.
- **Estimate progress from audio duration in the route**: rejected because BD-10 explicitly replaces frontend/client-side estimation with real worker progress.

## Decision 5: Reserve 100 for terminal success

**Decision**: Chunk-loop progress is capped at 99. `progress_percent=100` is emitted only after the transcription result is persisted and the session is marked transcribed.

**Rationale**: This prevents the UI from showing complete progress before the result is actually available.

**Alternatives considered**:

- **Allow chunk loop to emit 100**: rejected because completion of the last chunk is not the same as successful persistence and state transition.

## Decision 6: Keep current security boundary unchanged

**Decision**: The existing `GET /jobs/{job_id}` authentication and cross-campaign hiding behavior remains unchanged.

**Rationale**: Progress fields should not weaken authorization. Unknown or foreign jobs continue to be hidden through the existing not-found path.

**Alternatives considered**:

- **Expose progress through a public unauthenticated status endpoint**: rejected because job IDs can reveal workflow information and the current service already protects job polling.
