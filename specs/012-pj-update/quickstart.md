# Quickstart: BD-12 PJ Update

## Prerequisites

- Development dependencies installed with `pip install -e ".[dev]"`.
- Existing DB migrations applied.
- A GM web session or GM API key available.
- At least one campaign and PJ exist, or create them through existing endpoints.

## Implementation Checklist

1. Add `PjUpdate` to `app/services/jdr/schemas.py`.
2. Add a focused PJ update method to `PjRepository` or implement the update in
   `logic.update_pj` using the existing repository load method.
3. Add `logic.update_pj(...)` with omitted-vs-null handling for `user_id`.
4. Add `PATCH /services/jdr/pjs/{pj_id}` to `app/services/jdr/router.py`.
5. Add tests in `tests/services/jdr/test_pjs.py` for success and error cases.
6. Regenerate `docs/context/api/openapi.json`.
7. Update `docs/services/jdr.md`, `docs/memo.md`, and `docs/journal.md` during
   implementation completion.

## Focused Tests

```powershell
pytest tests/services/jdr/test_pjs.py -q
```

Recommended additional contract check:

```powershell
pytest tests/services/jdr/test_router_scaffold.py tests/services/jdr/test_pjs.py -q
```

## Full Validation

```powershell
ruff check .
pytest
docker compose config --quiet
```

## Manual Smoke Test

Create a PJ:

```powershell
curl -X POST http://localhost:8000/services/jdr/pjs `
  -H "Content-Type: application/json" `
  -H "Cookie: session=<cookie_session>" `
  -d '{"name":"Aragorn","campaign_id":"<campaign_uuid>"}'
```

Rename it:

```powershell
curl -X PATCH http://localhost:8000/services/jdr/pjs/<pj_id> `
  -H "Content-Type: application/json" `
  -H "Cookie: session=<cookie_session>" `
  -d '{"name":"Aragorn II"}'
```

Link a user:

```powershell
curl -X PATCH http://localhost:8000/services/jdr/pjs/<pj_id> `
  -H "Content-Type: application/json" `
  -H "Cookie: session=<cookie_session>" `
  -d '{"user_id":"<user_uuid>"}'
```

Unlink the user:

```powershell
curl -X PATCH http://localhost:8000/services/jdr/pjs/<pj_id> `
  -H "Content-Type: application/json" `
  -H "Cookie: session=<cookie_session>" `
  -d '{"user_id":null}'
```

Expected observations:

- Success returns `200` with `PjOut`.
- Rename updates only `name`.
- Link/unlink updates only `user_id`.
- Duplicate name returns `409 duplicate-pj`.
- Unknown non-null user returns `422 invalid-user`.
- Foreign PJ returns `404 pj-not-found`.

## OpenAPI Contract Check

After regenerating OpenAPI:

```powershell
rg '"/services/jdr/pjs/\\{pj_id\\}"|"PjUpdate"' docs/context/api/openapi.json
```
