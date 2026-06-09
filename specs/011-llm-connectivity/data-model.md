# Data Model: BD-11 LLM Connectivity

BD-11 reuses existing data structures. No schema migration is planned.

## Entity: LLM Provider Configuration

Source: environment variables read by `app/core/config.py`.

Fields:

- `LLM_PROVIDER`: configured provider key, e.g. `deepinfra`, `openai`,
  `groq`, `ollama`, `vllm`, `together`, or `mock`.
- `LLM_MODEL`: model identifier passed to the adapter.
- `LLM_API_KEY`: secret token for cloud providers; never committed.
- `LLM_BASE_URL`: optional override. When running in Docker Compose, this must
  be reachable from the `worker` container.
- `LLM_TIMEOUT_SECONDS`: SDK request timeout.
- `LLM_MAX_TOKENS_DEFAULT`: default completion budget for summary map/reduce.

Validation rules:

- Unknown providers are rejected by the adapter factory.
- Cloud providers require `LLM_API_KEY`.
- Local providers may use a placeholder key.
- BD-11 may add stricter validation if needed, but must not hardcode secrets or
  provider-specific service behavior.

## Entity: Summary Generation Job

Existing representation:

- Runtime state: RQ job enqueued by
  `POST /services/jdr/sessions/{session_id}/artifacts/summary`.
- API projection: `JobOut` returned by `GET /services/jdr/jobs/{job_id}`.
- Related SQL rows: existing JDR session/chunk/artifact/job tables where already
  used by the current service.

Fields exposed by API:

- `id`
- `kind = "summary"`
- `session_id`
- `status`: `queued`, `running`, `succeeded`, or `failed`
- `failure_reason`: nullable on non-failed jobs; non-empty when LLM exhaustion
  ends the job as failed
- `queued_at`
- `started_at`
- `ended_at`
- `phase`
- `progress_percent`

State transitions:

```text
queued -> running -> succeeded
queued -> running -> failed
running -> failed
```

LLM transient failures may be retried by the existing RQ enqueue policy. After
retry exhaustion, the public job projection must show `status="failed"` and a
non-empty `failure_reason`.

## Entity: Summary Artifact

Existing representation: `jdr_artifacts` row with `kind="summary"`.

Fields used by BD-11:

- `session_id`
- `kind = "summary"`
- `content_json.text`
- `model_used`
- `generated_at`

Rules:

- Created/updated only after successful map/reduce summary generation.
- Not created for failed LLM connectivity attempts.
- Fetch contract remains `GET /services/jdr/sessions/{session_id}/artifacts/summary`.

## Entity: Failure Reason

Existing representation: `JobOut.failure_reason`.

Rules:

- Must be `null` while queued/running/succeeded.
- Must be a non-empty string when the summary job fails because the LLM is
  unreachable or unavailable after retries.
- Should be concise enough for frontend display/logging and must avoid dumping a
  full traceback.
