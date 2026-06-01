# REST API Contract: Identity Refactor and PJ Campaign Scoping

## General Rules

- Protected routes keep existing authentication behavior.
- Public JSON must use `system_role`, not `profile`.
- Public campaign membership role values are `gm` and `pj`.
- Public JSON must not expose campaign role value `player`.
- Error responses use the existing Problem Details shape.
- Datetime fields keep the existing explicit timezone serialization contract.

## Auth / Current Identity

### GET `/services/jdr/auth/me`

Returns the signed-in web user's identity and active campaign context.

**Response 200**:

```json
{
  "user": {
    "id": "11111111-1111-1111-1111-111111111111",
    "username": "kenan",
    "system_role": "admin"
  },
  "active_campaign": {
    "id": "22222222-2222-2222-2222-222222222222",
    "name": "Campagne par defaut",
    "role": "gm",
    "character_id": null
  }
}
```

**Contract checks**:

- `user.system_role` is required and is `admin` or `user`.
- `active_campaign.role`, when present, is `gm` or `pj`.
- `profile` is absent.
- `player` is never returned as campaign role.

## User Management

All `/services/jdr/users/*` routes require a signed-in administrator.

### POST `/services/jdr/users`

**Request**:

```json
{
  "username": "alice",
  "system_role": "user",
  "password": "secret-password"
}
```

**Response 201**:

```json
{
  "id": "33333333-3333-3333-3333-333333333333",
  "username": "alice",
  "system_role": "user",
  "status": "active",
  "created_at": "2026-06-01T12:00:00+00:00",
  "updated_at": "2026-06-01T12:00:00+00:00"
}
```

**Errors**:

- `401`: unauthenticated.
- `403`: authenticated user is not `admin`; title clearly indicates admin privileges are required.
- `409`: duplicate username.
- `422`: invalid request.

### GET `/services/jdr/users`

**Response 200**:

```json
{
  "items": [
    {
      "id": "33333333-3333-3333-3333-333333333333",
      "username": "alice",
      "system_role": "user",
      "status": "active",
      "created_at": "2026-06-01T12:00:00+00:00",
      "updated_at": "2026-06-01T12:00:00+00:00"
    }
  ]
}
```

### PATCH `/services/jdr/users/{user_id}`

**Request**:

```json
{
  "system_role": "admin",
  "password": "new-secret-password"
}
```

All fields are optional; omitted fields are unchanged.

**Response 200**: same shape as user output.

### DELETE `/services/jdr/users/{user_id}`

**Response 204**: user removed/deactivated according to existing account lifecycle rules.

## Campaigns

Campaign behavior remains BD-6 with updated identity semantics.

### POST `/services/jdr/campaigns`

Any authenticated user may create a campaign.

**Request**:

```json
{
  "name": "Les Royaumes Brises",
  "description": "Campagne principale"
}
```

**Response 201**:

```json
{
  "id": "44444444-4444-4444-4444-444444444444",
  "name": "Les Royaumes Brises",
  "description": "Campagne principale",
  "role": "gm",
  "session_count": 0,
  "last_session_at": null,
  "created_at": "2026-06-01T12:00:00+00:00"
}
```

**Contract checks**:

- Does not require `system_role: admin`.
- Creator receives GM membership.

## Sessions

Session campaign scoping remains BD-6.

### POST `/services/jdr/sessions`

Requires campaign GM membership for `campaign_id`.

**Request**:

```json
{
  "title": "Session 13",
  "recorded_at": "2026-06-01T18:00:00Z",
  "campaign_id": "44444444-4444-4444-4444-444444444444",
  "transcription_mode": "diarised"
}
```

### GET `/services/jdr/sessions?campaign_id={uuid}`

Requires membership in the campaign. GM and PJ members may read according to existing endpoint rules; mutations still require GM membership.

## PJs

### POST `/services/jdr/pjs`

Creates a campaign-scoped PJ. `campaign_id` is optional for V1 compatibility and falls back to the current user's default campaign.

**Request with explicit campaign**:

```json
{
  "name": "Aelar",
  "campaign_id": "44444444-4444-4444-4444-444444444444",
  "user_id": "33333333-3333-3333-3333-333333333333"
}
```

**Request using V1 default-campaign fallback**:

```json
{
  "name": "Aelar"
}
```

**Response 201**:

```json
{
  "id": "55555555-5555-5555-5555-555555555555",
  "name": "Aelar",
  "campaign_id": "44444444-4444-4444-4444-444444444444",
  "user_id": "33333333-3333-3333-3333-333333333333",
  "created_at": "2026-06-01T12:00:00+00:00"
}
```

**Errors**:

- `403`: signed-in user is not GM of resolved campaign.
- `404`: explicit campaign or user assignment target does not exist or is not visible.
- `409`: duplicate PJ name in the relevant campaign/owner scope, if duplicate guard applies.
- `422`: no campaign can be resolved or request is invalid.

### GET `/services/jdr/pjs`

Returns PJs from all campaigns where the signed-in user is a member.

**Response 200**:

```json
{
  "items": [
    {
      "id": "55555555-5555-5555-5555-555555555555",
      "name": "Aelar",
      "campaign_id": "44444444-4444-4444-4444-444444444444",
      "user_id": "33333333-3333-3333-3333-333333333333",
      "created_at": "2026-06-01T12:00:00+00:00"
    }
  ],
  "total": 1,
  "page": 1,
  "size": 1
}
```

### GET `/services/jdr/pjs?campaign_id={uuid}`

Returns PJs for one campaign after membership check.

**Errors**:

- `403`: signed-in user is not a campaign member.
- `422`: invalid campaign id.

## Optional Future PJ Management

These endpoints are acknowledged by the handoff but are not required for BD-7 completion unless implementation chooses to include them cheaply.

### PATCH `/services/jdr/pjs/{pj_id}`

Potential body:

```json
{
  "name": "Aelar renamed",
  "user_id": null
}
```

Requires GM membership in the PJ's campaign.

### DELETE `/services/jdr/pjs/{pj_id}`

Requires GM membership in the PJ's campaign.

## OpenAPI Synchronization

After implementation, the generated public contract must expose:

- `system_role` in user schemas.
- no public `profile` user field.
- campaign role values `gm | pj`.
- `campaign_id` and optional `user_id` in PJ outputs.
- optional `campaign_id` and optional `user_id` in PJ create input.
