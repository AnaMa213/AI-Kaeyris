# Quickstart: Campaigns CRUD and Session Campaign Filter

This quickstart describes manual validation after BD-6 implementation.

## 1. Run focused tests

```powershell
$env:UV_CACHE_DIR='D:\Projets\dev\AI-Kaeyris\.uv-cache'
uv run pytest tests\services\jdr\test_campaigns_crud.py tests\services\jdr\test_campaign_sessions.py tests\services\jdr\test_campaign_isolation.py -q
```

Expected:

- Campaign CRUD tests pass.
- Session create requires `campaign_id`.
- Session list with `campaign_id` returns only sessions from that campaign.
- Cross-campaign access is rejected.

## 2. Run existing campaign/auth regression tests

```powershell
$env:UV_CACHE_DIR='D:\Projets\dev\AI-Kaeyris\.uv-cache'
uv run pytest tests\services\jdr\test_auth_me.py tests\services\jdr\test_campaign_memberships.py tests\core\test_campaign_context.py -q
```

Expected:

- `/auth/me` still returns active campaign.
- Existing membership/default campaign behavior remains valid.
- Legacy session backfill remains idempotent.

## 3. Create a campaign manually

Start the API locally, sign in with a web session, then call:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/services/jdr/campaigns" `
  -WebSession $session `
  -ContentType "application/json" `
  -Body '{"name":"Les Royaumes Brises","description":"Campagne principale"}'
```

Expected:

- Response status is `201`.
- `role` is `gm`.
- `session_count` is `0`.
- `last_session_at` is null.
- `created_at` has `Z` or a numeric timezone offset.

## 4. List campaigns

```powershell
Invoke-RestMethod `
  -Method Get `
  -Uri "http://localhost:8000/services/jdr/campaigns" `
  -WebSession $session
```

Expected:

- Only campaigns where the signed-in user is a member are returned.
- Each item includes role, session count, latest session date, and creation date.

## 5. Create a session in a campaign

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/services/jdr/sessions" `
  -WebSession $session `
  -ContentType "application/json" `
  -Body '{"title":"Session 13 - La crypte oubliee","recorded_at":"2026-05-31T18:00:00Z","transcription_mode":"non_diarised","campaign_id":"11111111-1111-1111-1111-111111111111"}'
```

Expected:

- Response status is `201`.
- Missing `campaign_id` returns `422`.
- Non-GM campaign membership returns `403`.

## 6. Filter sessions by campaign

```powershell
Invoke-RestMethod `
  -Method Get `
  -Uri "http://localhost:8000/services/jdr/sessions?campaign_id=11111111-1111-1111-1111-111111111111" `
  -WebSession $session
```

Expected:

- All returned sessions belong to the requested campaign.
- Filtering by a campaign where the user is not a member returns `403`.
- Calling without `campaign_id` keeps existing behavior for backward compatibility.

## 7. Delete campaign behavior

Delete an empty campaign:

```powershell
Invoke-RestMethod `
  -Method Delete `
  -Uri "http://localhost:8000/services/jdr/campaigns/11111111-1111-1111-1111-111111111111" `
  -WebSession $session
```

Expected:

- Empty campaign deletion returns `204`.
- Campaign with sessions returns `409`.
- Player membership returns `403`.

## 8. Contract and quality gates

```powershell
$env:UV_CACHE_DIR='D:\Projets\dev\AI-Kaeyris\.uv-cache'
uv run pytest
uv run ruff check .
```

Also verify OpenAPI includes:

- `GET /services/jdr/campaigns`
- `POST /services/jdr/campaigns`
- `GET /services/jdr/campaigns/{campaign_id}`
- `PATCH /services/jdr/campaigns/{campaign_id}`
- `DELETE /services/jdr/campaigns/{campaign_id}`
- `campaign_id` query parameter on `GET /services/jdr/sessions`
- required `campaign_id` field on `POST /services/jdr/sessions`
