# Quickstart: Campaign Auth Context

Manual validation scenario for BD-4 after implementation.

## 0. Setup local

```powershell
alembic upgrade head
uvicorn app.main:app --reload
```

Expected:

- the migration creates/preserves the V1 default campaign;
- existing users have one campaign membership;
- `ruff check .` and `pytest` still pass before manual testing.

## 1. Login as GM and fetch `/auth/me`

```powershell
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession

Invoke-WebRequest -Method POST `
  -Uri "http://localhost:8000/services/jdr/auth/login" `
  -WebSession $session `
  -ContentType "application/json" `
  -Body (@{
    username = "admin"
    profile = "gm"
    password = "choose-a-local-password"
  } | ConvertTo-Json) `
  -UseBasicParsing

$me = Invoke-RestMethod -Method GET `
  -Uri "http://localhost:8000/services/jdr/auth/me" `
  -WebSession $session

$me.user.username
$me.active_campaign.role
```

Expected:

- HTTP 200;
- `user.id` and `user.username` are present;
- `active_campaign.id` is present;
- GM user has role `mj`;
- response includes `Cache-Control: no-store`.

## 2. Create a player user and verify membership context

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
```

Login as the player:

```powershell
$playerSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession

Invoke-WebRequest -Method POST `
  -Uri "http://localhost:8000/services/jdr/auth/login" `
  -WebSession $playerSession `
  -ContentType "application/json" `
  -Body (@{
    username = "player1"
    profile = "user"
    password = "local-test-password"
  } | ConvertTo-Json) `
  -UseBasicParsing

$playerMe = Invoke-RestMethod -Method GET `
  -Uri "http://localhost:8000/services/jdr/auth/me" `
  -WebSession $playerSession

$playerMe.active_campaign.role
```

Expected: `player`.

## 3. Create JDR data without `campaign_id`

```powershell
$createdSession = Invoke-RestMethod -Method POST `
  -Uri "http://localhost:8000/services/jdr/sessions" `
  -WebSession $session `
  -ContentType "application/json" `
  -Body (@{
    title = "Session BD-4"
    recorded_at = "2026-05-30T20:00:00Z"
  } | ConvertTo-Json)

$sessions = Invoke-RestMethod -Method GET `
  -Uri "http://localhost:8000/services/jdr/sessions" `
  -WebSession $session
```

Expected:

- request body does not contain `campaign_id`;
- created session belongs to the active campaign server-side;
- list includes the created session.

## 4. Two-campaign isolation check

Create a second campaign and a foreign session directly in SQL or a test fixture. Do not expose a campaign creation endpoint for this step.

Expected:

- authenticated user from campaign 1 does not see campaign 2 sessions in `GET /services/jdr/sessions`;
- direct access to a campaign 2 session id returns the existing not-found/forbidden behavior;
- creating a new session still assigns campaign 1 automatically.

## 5. Required validation

```powershell
ruff check .
pytest
docker compose config
```

Manual curl/browser validation:

- `GET /services/jdr/auth/me` returns the documented schema for a MJ and for a player.
- Frontend can remove its `/auth/me` mock without changing consuming components.
