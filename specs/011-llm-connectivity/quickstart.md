# Quickstart: BD-11 LLM Connectivity

This quickstart validates the Docker/worker path for non-diarised summary
generation. Use placeholder values in documentation; keep real secrets in
local `.env` only.

## 1. Confirm current branch and dependencies

```powershell
git status --short --branch
uv sync
```

Expected:

- Branch is `codex/011-llm-connectivity`.
- No real secret is staged or committed.

## 2. Configure LLM environment

In local `.env`, set the provider values used by both API and worker:

```dotenv
LLM_PROVIDER=deepinfra
LLM_MODEL=meta-llama/Meta-Llama-3.1-8B-Instruct
LLM_API_KEY=<local secret>
LLM_BASE_URL=
LLM_TIMEOUT_SECONDS=60
```

If using a local OpenAI-compatible server from Docker Compose, do not point the
worker at container-local `localhost` unless the LLM server runs in the worker
container. Prefer one of:

```dotenv
LLM_BASE_URL=http://host.docker.internal:11434/v1
```

or a Compose service name, for example:

```dotenv
LLM_BASE_URL=http://ollama:11434/v1
```

## 3. Start the stack

```powershell
docker compose up --build
```

In another shell, confirm both containers see the same LLM settings:

```powershell
docker compose exec api python -c "from app.core.config import settings; print(settings.LLM_PROVIDER, settings.LLM_BASE_URL)"
docker compose exec worker python -c "from app.core.config import settings; print(settings.LLM_PROVIDER, settings.LLM_BASE_URL)"
```

Expected: provider/base URL match between `api` and `worker`.

## 4. Run automated checks

```powershell
ruff check .
pytest
```

Expected: both commands pass.

## 5. Validate success path manually

Use the existing authenticated JDR flow to create/upload/transcribe a
non-diarised session, then queue the summary:

```powershell
curl -X POST `
  -H "Authorization: Bearer <gm-token>" `
  http://localhost:8000/services/jdr/sessions/<session-id>/artifacts/summary
```

Poll the returned job:

```powershell
curl -H "Authorization: Bearer <gm-token>" `
  http://localhost:8000/services/jdr/jobs/<job-id>
```

Expected:

- Job eventually reaches `status="succeeded"`.
- `failure_reason` stays `null`.
- `GET /services/jdr/sessions/<session-id>/artifacts/summary` returns text.

## 6. Validate failure path manually

Temporarily set an unreachable local base URL in `.env`, then restart API and
worker:

```dotenv
LLM_BASE_URL=http://host.docker.internal:9/v1
```

Queue another summary job and poll it after retries are exhausted.

Expected:

- Job reaches `status="failed"`.
- `failure_reason` is a non-empty string mentioning the connection failure.
- No summary artifact is created for that failed attempt.
