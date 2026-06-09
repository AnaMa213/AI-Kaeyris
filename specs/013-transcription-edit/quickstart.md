# Quickstart: BD-13 Transcription Edit

## Goal

Verify that a GM can save a corrected Markdown transcription, read it back, and
generate a summary from the corrected text.

## Prerequisites

- Backend dependencies installed.
- Database migrated to the BD-13 migration.
- A GM credential or web session available.
- A JDR session owned by that GM in `transcribed` state.

## Run Targeted Checks

```powershell
uv run ruff check .
uv run pytest tests/services/jdr/test_transcription_edit.py -q
uv run pytest tests/jobs/test_jdr_summary.py -q
```

## Manual API Flow

Set variables:

```powershell
$base = "http://localhost:8000"
$token = "<gm-bearer-token>"
$sessionId = "<transcribed-session-id>"
$headers = @{ Authorization = "Bearer $token" }
```

Save edited Markdown:

```powershell
$body = @{
  content_md = "## Scène corrigée`n`n**Aldric** : phrase corrigée distinctive."
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Put `
  -Uri "$base/services/jdr/sessions/$sessionId/transcription" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

Read Markdown export:

```powershell
Invoke-WebRequest `
  -Method Get `
  -Uri "$base/services/jdr/sessions/$sessionId/transcription.md" `
  -Headers $headers |
  Select-Object -ExpandProperty Content
```

Expected: the response body is exactly the edited Markdown saved above.

Launch summary generation after the edit:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "$base/services/jdr/sessions/$sessionId/artifacts/summary" `
  -Headers $headers
```

Poll the returned job with the existing job-status flow until it succeeds, then
read the summary:

```powershell
Invoke-RestMethod `
  -Method Get `
  -Uri "$base/services/jdr/sessions/$sessionId/artifacts/summary" `
  -Headers $headers
```

Expected: tests should prove the generated source consumed the corrected text.
The same source-selection rule applies to narrative, elements, and POV
generation jobs launched after the edit.

## Negative Checks

Save blank content:

```powershell
Invoke-RestMethod `
  -Method Put `
  -Uri "$base/services/jdr/sessions/$sessionId/transcription" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body (@{ content_md = "   " } | ConvertTo-Json)
```

Expected: validation error.

Save on a non-transcribed session:

```powershell
Invoke-RestMethod `
  -Method Put `
  -Uri "$base/services/jdr/sessions/<non-transcribed-session-id>/transcription" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

Expected: `409 session-not-transcribed`.

Save on another GM's session:

```powershell
Invoke-RestMethod `
  -Method Put `
  -Uri "$base/services/jdr/sessions/<other-gm-session-id>/transcription" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

Expected: `404 session-not-found`.

## Documentation Checks

```powershell
uv run python -c "import json;from app.main import app;open('docs/context/api/openapi.json','w',encoding='utf-8').write(json.dumps(app.openapi(),ensure_ascii=False,indent=2,sort_keys=True))"
git diff -- docs/context/api/openapi.json
```

Expected: OpenAPI includes the new `PUT /services/jdr/sessions/{session_id}/transcription`
operation and `content_md` request schema.
