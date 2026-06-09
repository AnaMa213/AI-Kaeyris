# Quickstart: Delete JDR Session

## Focused Validation

```powershell
uv run pytest tests/services/jdr/test_sessions_delete.py -q
uv run pytest tests/services/jdr/test_sessions.py tests/services/jdr/test_campaigns_crud.py -q
uv run ruff check .
```

## Full Validation

```powershell
uv run pytest -q
docker compose config --quiet
```

## Manual API Check

Create or identify a GM-owned session, then delete it:

```powershell
$base = "http://localhost:8000"
$token = "<gm-token>"
$sessionId = "<session-uuid>"

curl.exe -i -X DELETE "$base/services/jdr/sessions/$sessionId" `
  -H "Authorization: Bearer $token"
```

Expected:

```http
HTTP/1.1 204 No Content
```

Verify the session is gone:

```powershell
curl.exe -i "$base/services/jdr/sessions/$sessionId" `
  -H "Authorization: Bearer $token"
```

Expected:

```http
HTTP/1.1 404 Not Found
```

## Active Work Check

Try to delete a session with active transcription or an observable active current RQ job.

Expected:

```http
HTTP/1.1 409 Conflict
```

The session should remain readable.

## OpenAPI Check

After regenerating OpenAPI:

```powershell
rg 'delete_session|/services/jdr/sessions/\\{session_id\\}' docs/context/api/openapi.json
```
