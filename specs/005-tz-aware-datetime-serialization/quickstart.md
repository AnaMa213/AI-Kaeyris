# Quickstart: Timezone-Aware Datetime Serialization

## Goal

Prove that backend JSON responses no longer emit timezone-naive datetime strings.

## Suggested Implementation Flow

1. Add focused failing tests for the current bug.
2. Add a shared datetime serializer helper that:
   - leaves `None` unchanged,
   - treats naive datetimes as UTC,
   - converts aware datetimes to UTC,
   - emits an ISO-8601 string with an explicit timezone suffix.
3. Wire the helper into Pydantic response schemas that expose datetime fields.
4. Re-run the targeted tests.
5. Run the normal quality checks.

## Targeted Test Commands

```powershell
$env:UV_CACHE_DIR='D:\Projets\dev\AI-Kaeyris\.uv-cache'; uv run pytest tests\core\test_datetime_serialization.py tests\services\jdr\test_datetime_serialization.py -q
```

If the implementation extends existing route tests instead of creating one route-specific file:

```powershell
$env:UV_CACHE_DIR='D:\Projets\dev\AI-Kaeyris\.uv-cache'; uv run pytest tests\services\jdr\test_sessions.py tests\services\jdr\test_pjs.py tests\services\jdr\test_auth_me.py -q
```

Full checks before considering the feature done:

```powershell
$env:UV_CACHE_DIR='D:\Projets\dev\AI-Kaeyris\.uv-cache'; uv run pytest
```

```powershell
$env:UV_CACHE_DIR='D:\Projets\dev\AI-Kaeyris\.uv-cache'; uv run ruff check .
```

## Manual Contract Probe

Create a session with a UTC timestamp:

```bash
curl -X POST http://localhost:8000/services/jdr/sessions \
  -H "Content-Type: application/json" \
  -b cookies.txt \
  -d '{"title":"DIAGNOSTIC-TZ","recorded_at":"2026-05-31T18:00:00.000Z","transcription_mode":"non_diarised"}'
```

Expected response shape:

```json
{
  "recorded_at": "2026-05-31T18:00:00+00:00",
  "created_at": "2026-05-31T21:24:17.740107+00:00",
  "updated_at": "2026-05-31T21:24:17.740109+00:00"
}
```

`Z` is also acceptable instead of `+00:00`.

## Done Criteria for This Feature

- Session create/detail/list responses include explicit timezone suffixes.
- PJ create/list responses include explicit timezone suffixes.
- User create/list/update responses include explicit timezone suffixes for non-null datetime fields.
- Auth context responses follow the same rule if datetime fields are present.
- Existing datetime input formats still pass.
- `pytest` and `ruff check .` pass before delivery.
