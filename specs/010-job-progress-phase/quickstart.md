# Quickstart: JDR Job Progress Phase

## Prerequisites

- Development dependencies installed with `pip install -e ".[dev]"`.
- Redis/fakeredis-backed tests available as in the existing JDR job tests.
- Existing database migrations already applied.

## Implementation Checklist

1. Add `phase` and `progress_percent` to `JobOut`.
2. Add a local progress emitter around the transcription RQ job path.
3. Add an optional `(chunks_done, chunks_total)` callback to `_transcribe_with_optional_chunking`.
4. Read progress metadata in `GET /services/jdr/jobs/{job_id}` and map invalid/missing metadata to nullable fields.
5. Add tests for queued/null, running progress, done progress, failed-last-progress, metadata fallback, and callback monotonicity.
6. Regenerate `docs/context/api/openapi.json`.
7. Update JDR docs/memo/journal as part of implementation completion.

## Focused Tests

```powershell
pytest tests/services/jdr/test_jobs_route.py -q
pytest tests/jobs/test_jdr_summary.py -q
```

## Full Validation

```powershell
ruff check .
pytest
```

## Manual Smoke Test

Start the stack:

```powershell
docker compose up --build
```

In another terminal, create or reuse a JDR session with audio, then poll the returned job id:

```powershell
curl http://localhost:8000/services/jdr/jobs/<job_id> `
  -H "Authorization: Bearer <gm_token>"
```

Expected observations:

- Before the worker starts: `status="queued"`, `phase=null`, `progress_percent=null`.
- During transcription: `status="running"`, `phase="transcribing"`, `progress_percent` from 0 to 99.
- After success: `status="succeeded"`, `phase="done"`, `progress_percent=100`.
- If progress metadata is absent or expired: the endpoint still returns `200` for valid jobs and the two progress fields are `null`.

## Contract Check

After regenerating OpenAPI, verify `JobOut` contains the new nullable fields:

```powershell
rg '"phase"|"progress_percent"' docs/context/api/openapi.json
```

## Out of Scope

- No SSE endpoint in this phase.
- No WebSocket progress stream.
- No database migration for progress history.
- No frontend label/localization work in the backend repository.
