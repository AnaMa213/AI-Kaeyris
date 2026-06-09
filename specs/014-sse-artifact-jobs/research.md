# Research: Live Job Events

## Decision 1: Use Server-Sent Events for one-way job updates

**Decision**: Expose job updates as Server-Sent Events using `text/event-stream`.

**Rationale**: BD-14 needs server-to-client status pushes, not bidirectional collaboration. The HTML Standard defines server-sent event streams with the `text/event-stream` MIME type, and the handoff explicitly asks for this transport. Source: https://html.spec.whatwg.org/multipage/server-sent-events.html

**Alternatives considered**:

- **WebSocket**: rejected because BD-14 is one-way status delivery and WebSocket would add lifecycle, proxy, and test complexity.
- **Keep only polling**: rejected because the feature value is reducing polling latency for artifact jobs.
- **Long-polling**: rejected because SSE is already the requested contract and has a standard event format.

## Decision 2: Use existing streaming response primitives

**Decision**: Implement the endpoint with the framework's existing streaming response support rather than adding a new SSE package.

**Rationale**: Starlette documents `StreamingResponse` for async generators, and FastAPI documents that streamed chunks are sent as yielded. This is enough for simple SSE frames without bringing in a new dependency. Sources: https://starlette.dev/responses/ and https://fastapi.tiangolo.com/advanced/stream-data/

**Alternatives considered**:

- **Add `sse-starlette`**: rejected for YAGNI; BD-14 only needs a small stream of already-serialized progress events.
- **Hand-build a raw ASGI app**: rejected because it bypasses the existing router/auth dependency shape.

## Decision 3: Reuse `JobOut` projection as the single payload source

**Decision**: Build every SSE payload from the same job projection used by `GET /services/jdr/jobs/{job_id}`.

**Rationale**: The existing route already maps RQ status to public `JobStatus`, validates metadata, extracts `failure_reason`, and enforces GM/campaign visibility. Reusing that projection prevents drift between polling and SSE.

**Alternatives considered**:

- **Create a separate SSE-specific projection**: rejected because it risks different status, auth, or failure semantics.
- **Stream raw RQ status**: rejected because frontend contracts should remain stable and backend-specific statuses are already normalized.

## Decision 4: Poll existing RQ state from the API process

**Decision**: The SSE generator will re-read the job at a short interval, about one second, using existing Redis/RQ state.

**Rationale**: RQ stores live job state in Redis, supports job metadata, and BD-10 already uses refreshed job metadata for progress. Polling inside the stream avoids introducing Redis pub/sub or a new persisted stream table. Source: https://python-rq.org/docs/jobs/

**Alternatives considered**:

- **Redis pub/sub from workers**: rejected because it introduces a second event channel and missed-message behavior for late subscribers.
- **Persist progress history to SQL**: rejected because BD-14 has no migration and terminal status already lives in existing job state.

## Decision 5: Terminal event closes the stream

**Decision**: Emit one final `progress` event when `status` becomes `succeeded` or `failed`, then stop the event generator.

**Rationale**: The frontend needs a clear end-of-job signal and should not keep idle connections open after completion. This matches the handoff's terminal-event requirement.

**Alternatives considered**:

- **Keep streaming heartbeat after terminal state**: rejected because the job is complete and polling fallback can read the final state later.
- **Close without terminal event**: rejected because clients could miss the final state if their last in-flight event was still `running`.

## Decision 6: Document SSE explicitly in OpenAPI

**Decision**: Add an OpenAPI-visible `GET /services/jdr/jobs/{job_id}/events` response with media type `text/event-stream` and a description of the event frame format.

**Rationale**: FastAPI notes that directly returned response objects may not be documented unless the route declares response metadata. BD-14 requires frontend contract discovery, so the route must be documented intentionally. Source: https://fastapi.tiangolo.com/advanced/custom-response/

**Alternatives considered**:

- **Rely only on prose docs**: rejected because frontend type/contract generation needs the OpenAPI artifact.
- **Model every SSE frame as a normal JSON response**: rejected because the media type is a stream of event frames, not a single JSON object.
