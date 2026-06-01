# Quickstart: Identity Refactor and PJ Campaign Scoping

This quickstart describes validation after BD-7 implementation.

## 1. Run identity and membership tests

```powershell
$env:UV_CACHE_DIR='D:\Projets\dev\AI-Kaeyris\.uv-cache'
uv run pytest tests\core tests\services\jdr\test_auth_me.py tests\services\jdr\test_user_management.py tests\services\jdr\test_campaign_memberships.py -q
```

Expected:

- User schemas expose `system_role`.
- Non-admin users cannot manage accounts.
- Standard users can create campaigns.
- Campaign member role values are `gm` and `pj`.
- `/auth/me` returns `user.system_role`.

## 2. Run PJ scoping tests

```powershell
$env:UV_CACHE_DIR='D:\Projets\dev\AI-Kaeyris\.uv-cache'
uv run pytest tests\services\jdr\test_pjs.py tests\services\jdr\test_campaign_sessions.py tests\services\jdr\test_player_access.py tests\services\jdr\test_player_listing.py -q
```

Expected:

- New PJs always have `campaign_id`.
- `POST /services/jdr/pjs` accepts explicit `campaign_id`.
- `POST /services/jdr/pjs` without `campaign_id` falls back to the user's default campaign.
- `GET /services/jdr/pjs` without filter returns PJs from campaigns where the user is a member.
- `GET /services/jdr/pjs?campaign_id=...` filters by campaign and rejects non-members.

## 3. Validate setup/reseed behavior on an empty database

Start from a purged local/staging DB, apply migrations, then run the accepted local/staging seed path.

Expected:

- One administrator user exists.
- One default campaign exists.
- The administrator is GM of that campaign.
- The administrator has that campaign as default.
- No production secret or universal credential is silently enabled outside explicit local/staging setup.

## 4. Manual auth/me check

Sign in as the seeded admin, then call:

```powershell
Invoke-RestMethod `
  -Method Get `
  -Uri "http://localhost:8000/services/jdr/auth/me" `
  -WebSession $session
```

Expected shape:

```json
{
  "user": {
    "id": "...",
    "username": "admin",
    "system_role": "admin"
  },
  "active_campaign": {
    "id": "...",
    "name": "...",
    "role": "gm",
    "character_id": null
  }
}
```

## 5. Manual user-management authorization check

Create or sign in as a standard user, then call:

```powershell
Invoke-RestMethod `
  -Method Get `
  -Uri "http://localhost:8000/services/jdr/users" `
  -WebSession $standardUserSession
```

Expected:

- Response is `403`.
- Problem title clearly indicates administrator privileges are required.

Then call the same endpoint as admin.

Expected:

- Response is `200`.
- User items contain `system_role`.
- User items do not contain `profile`.

## 6. Manual campaign creation by standard user

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/services/jdr/campaigns" `
  -WebSession $standardUserSession `
  -ContentType "application/json" `
  -Body '{"name":"Campagne standard user","description":"Validation BD-7"}'
```

Expected:

- Response is `201`.
- Response role is `gm`.
- No admin privilege is required.

## 7. Manual PJ creation compatibility check

Create a PJ without `campaign_id` as a GM whose default campaign is set:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/services/jdr/pjs" `
  -WebSession $session `
  -ContentType "application/json" `
  -Body '{"name":"Aelar"}'
```

Expected:

- Response is `201`.
- Response includes non-null `campaign_id`.
- Response includes `user_id`, either null or the assigned user id.

Create a PJ with explicit campaign:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/services/jdr/pjs" `
  -WebSession $session `
  -ContentType "application/json" `
  -Body '{"name":"Maelis","campaign_id":"11111111-1111-1111-1111-111111111111","user_id":null}'
```

Expected:

- Response is `201` if the caller is GM of that campaign.
- Response is `403` if the caller is not GM of that campaign.

## 8. Contract and quality gates

```powershell
$env:UV_CACHE_DIR='D:\Projets\dev\AI-Kaeyris\.uv-cache'
uv run ruff check .
uv run pytest -q
docker compose config
```

Also verify OpenAPI includes:

- `system_role` in setup/login user-management schemas and auth/me user output.
- no public `profile` field in user schemas.
- campaign role values `gm` and `pj`.
- `PjCreate.campaign_id` optional.
- `PjCreate.user_id` optional.
- `PjOut.campaign_id` required.
- `PjOut.user_id` optional.
