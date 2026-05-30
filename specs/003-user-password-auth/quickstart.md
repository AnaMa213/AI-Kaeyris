# Quickstart: User Password Authentication

Manual validation scenario for the feature after implementation.

## 0. Setup local

```powershell
alembic upgrade head

$env:WEB_SESSION_TTL_SECONDS = "28800"
$env:SESSION_COOKIE_NAME = "session"
$env:SESSION_COOKIE_SECURE = "false"
$env:SESSION_COOKIE_SAMESITE = "lax"

uvicorn app.main:app --reload
```

## 1. First-run setup from the front contract

When the database has no user yet:

```powershell
$setupStatus = Invoke-RestMethod -Method GET `
  -Uri "http://localhost:8000/services/jdr/auth/setup/status"

$setupStatus.required   # -> true
```

Create the first GM from the front:

```powershell
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession

$admin = Invoke-RestMethod -Method POST `
  -Uri "http://localhost:8000/services/jdr/auth/setup" `
  -WebSession $session `
  -ContentType "application/json" `
  -Body (@{
    username = "admin"
    password = "choose-a-local-password"
  } | ConvertTo-Json) `

$admin.profile   # -> gm
```

Expected:

- HTTP 201
- `Set-Cookie` contains `session=...`, `HttpOnly`, `Path=/`, `SameSite=Lax`
- a second `POST /setup` now returns 409

## 2. Create a regular user

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

$created.username
$created.profile
```

Expected: `player1`, `user`, no password hash in response.

## 3. Login as the new user

```powershell
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
```

Expected: HTTP 200 and a session cookie.

## 4. Wrong credentials return exact front contract

```powershell
try {
  Invoke-WebRequest -Method POST `
    -Uri "http://localhost:8000/services/jdr/auth/login" `
    -ContentType "application/json" `
    -Body (@{
      username = "player1"
      profile = "user"
      password = "wrong"
    } | ConvertTo-Json) `
    -UseBasicParsing
} catch {
  $_.Exception.Response.StatusCode.value__
}
```

Expected:

- HTTP 401
- `Content-Type: application/problem+json`
- body:

```json
{"type":"about:blank","title":"Invalid credentials","status":401}
```

## 5. Change password

```powershell
Invoke-RestMethod -Method PATCH `
  -Uri "http://localhost:8000/services/jdr/users/$($created.id)" `
  -WebSession $session `
  -ContentType "application/json" `
  -Body (@{ password = "new-local-test-password" } | ConvertTo-Json)
```

Expected:

- old password login now returns 401
- new password login returns 200

## 6. Logical delete

```powershell
Invoke-WebRequest -Method DELETE `
  -Uri "http://localhost:8000/services/jdr/users/$($created.id)" `
  -WebSession $session `
  -UseBasicParsing
```

Expected:

- HTTP 204
- future login for `player1` returns 401
- user still appears in admin list with status `deleted`

## 7. Logout

```powershell
Invoke-WebRequest -Method POST `
  -Uri "http://localhost:8000/services/jdr/auth/logout" `
  -WebSession $session `
  -UseBasicParsing
```

Expected:

- HTTP 204
- old cookie no longer authenticates protected routes

## 8. Required validation

```powershell
ruff check .
pytest
```

Also perform one manual browser call from the front with `credentials: "include"` and confirm the cookie is stored as HTTP-only.
