# Quickstart: Live Job Events

## Prerequisites

- A GM token.
- A known JDR job id returned by an artifact or transcription endpoint.
- The API running locally.

## Focused Validation

```powershell
uv run pytest tests/services/jdr/test_jobs_route.py -q
uv run ruff check .
```

## Full Validation

```powershell
uv run pytest -q
docker compose config --quiet
```

## Manual SSE Check

Start a job, then subscribe to the event stream:

```powershell
$base = "http://localhost:8000"
$token = "<gm-token>"
$jobId = "<job-id>"

curl.exe -N "$base/services/jdr/jobs/$jobId/events" `
  -H "Authorization: Bearer $token" `
  -H "Accept: text/event-stream"
```

Expected frames while the job is active:

```text
event: progress
data: {"status":"running","phase":null,"progress_percent":null}

```

Expected terminal success frame:

```text
event: progress
data: {"status":"succeeded","phase":null,"progress_percent":null}

```

The connection should close after a `succeeded` or `failed` frame.

## Polling Fallback Check

The existing polling endpoint must still work for the same job:

```powershell
curl.exe "$base/services/jdr/jobs/$jobId" `
  -H "Authorization: Bearer $token"
```

Expected: unchanged `JobOut` JSON with `status`, `phase`, `progress_percent`, and `failure_reason`.

## OpenAPI Check

After regenerating OpenAPI:

```powershell
rg 'jobs/\\{job_id\\}/events|text/event-stream|event: progress' docs/context/api/openapi.json
```
