# Quickstart: Campaign Auth Context

Manual validation scenario for BD-4 after implementation.

## 0. Setup local

```powershell
alembic upgrade head

$env:WEB_SESSION_TTL_SECONDS = "28800"
$env:SESSION_COOKIE_NAME = "session"
$env:SESSION_COOKIE_SECURE = "false"
$env:SESSION_COOKIE_SAMESITE = "lax"

uvicorn app.main:app --reload
```

## 1. First-run setup creates campaign context

On an empty database:

```powershell
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession

Invoke-RestMethod -Method POST `
  -Uri "http://localhost:8000/services/jdr/auth/setup" `
  -WebSession $session `
  -ContentType "application/json" `
  -Body (@{
    username = "admin"
    password = "choose-a-local-password"
  } | ConvertTo-Json)
```

Expected:

- HTTP 201
- `session` cookie is set
- first GM user exists
- V1 default campaign exists
- first GM is member of the default campaign with role `gm`

## 2. `/auth/me` after setup

```powershell
$me = Invoke-RestMethod -Method GET `
  -Uri "http://localhost:8000/services/jdr/auth/me" `
  -WebSession $session

$me.user.username
$me.active_campaign.role
```

Expected:

- `user.username` is `admin`
- `active_campaign.id` is present
- `active_campaign.name` is present
- `active_campaign.role` is `gm`
- `active_campaign.character_id` is `$null`

## 3. Create a user and verify membership

```powershell
$created = Invoke-RestMethod -Method POST `
  -Uri "http://localhost:8000/services/jdr/users" `
  -WebSession $session `
  -ContentType "application/json" `
  -Body (@{
    username = "player1"
    profile = "user"
    password = "local-test-password"
  } | ConvertTo-Json)

$userSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession

Invoke-WebRequest -Method POST `
  -Uri "http://localhost:8000/services/jdr/auth/login" `
  -WebSession $userSession `
  -ContentType "application/json" `
  -Body (@{
    username = "player1"
    profile = "user"
    password = "local-test-password"
  } | ConvertTo-Json) `
  -UseBasicParsing

$playerMe = Invoke-RestMethod -Method GET `
  -Uri "http://localhost:8000/services/jdr/auth/me" `
  -WebSession $userSession
```

Expected:

- `playerMe.user.username` is `player1`
- `playerMe.active_campaign.id` equals the admin campaign id
- `playerMe.active_campaign.role` is `player`
- `playerMe.active_campaign.character_id` is `$null` until a character binding exists

## 4. Create scoped JDR data

```powershell
$sessionOut = Invoke-RestMethod -Method POST `
  -Uri "http://localhost:8000/services/jdr/sessions" `
  -WebSession $session `
  -ContentType "application/json" `
  -Body (@{
    title = "Session BD-4"
    recorded_at = "2026-05-31T20:30:00+00:00"
  } | ConvertTo-Json)

$pj = Invoke-RestMethod -Method POST `
  -Uri "http://localhost:8000/services/jdr/pjs" `
  -WebSession $session `
  -ContentType "application/json" `
  -Body (@{
    name = "Aelar"
  } | ConvertTo-Json)
```

Expected:

- Neither request sends `campaign_id`
- Both rows are internally attached to the active campaign
- Existing response shapes remain compatible

## 5. Isolation smoke test

Create a second campaign and second-campaign session directly through a test fixture or SQL setup. Then call:

```powershell
$sessions = Invoke-RestMethod -Method GET `
  -Uri "http://localhost:8000/services/jdr/sessions" `
  -WebSession $session
```

Expected:

- The default-campaign session is visible
- The second-campaign session is not visible
- Direct access to the second-campaign session id returns the existing not-found/forbidden behavior without revealing cross-campaign existence

## 6. Invalid session checks

```powershell
Invoke-WebRequest -Method POST `
  -Uri "http://localhost:8000/services/jdr/auth/logout" `
  -WebSession $session `
  -UseBasicParsing

try {
  Invoke-WebRequest -Method GET `
    -Uri "http://localhost:8000/services/jdr/auth/me" `
    -WebSession $session `
    -UseBasicParsing
} catch {
  $_.Exception.Response.StatusCode.value__
}
```

Expected: HTTP 401.

## 7. Required validation

```powershell
ruff check .
pytest tests/services/jdr/test_auth_me.py `
  tests/services/jdr/test_campaign_memberships.py `
  tests/services/jdr/test_campaign_isolation.py `
  tests/core/test_campaign_context.py
pytest
```

Also verify that the generated OpenAPI includes `GET /services/jdr/auth/me` and that existing login/logout/users request bodies are unchanged.
