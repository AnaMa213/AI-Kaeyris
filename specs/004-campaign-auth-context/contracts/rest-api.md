# Contracts: REST API - Campaign Auth Context

**Phase 1 du `/speckit-plan`**. REST surface added or constrained by BD-4.

All error bodies use the platform Problem Details format (`application/problem+json`) unless explicitly noted otherwise.

## 1. Current authenticated context

### `GET /services/jdr/auth/me`

Returns the current web session identity and active JDR campaign context.

Authentication:

- Requires a valid browser `session` cookie.
- Missing, expired, revoked, or deleted-user sessions return 401.
- Legacy Bearer API keys are not required for this endpoint; operational JDR endpoints keep supporting Bearer auth.

Request body: none.

Query parameters: none.

**Success with active campaign: 200**

```json
{
  "user": {
    "id": "11111111-2222-3333-4444-555555555555",
    "username": "kenan"
  },
  "active_campaign": {
    "id": "00000000-0000-0000-0000-000000000001",
    "name": "Campagne par defaut",
    "role": "gm",
    "character_id": null
  }
}
```

**Success without campaign membership: 200**

```json
{
  "user": {
    "id": "11111111-2222-3333-4444-555555555555",
    "username": "kenan"
  },
  "active_campaign": null
}
```

**Unauthorized: 401**

```json
{
  "type": "https://errors.ai-kaeyris.local/unauthorized",
  "title": "Unauthorized",
  "status": 401,
  "detail": "Missing or malformed credentials."
}
```

Notes:

- `role` accepted values: `gm`, `player`.
- `character_id` is `null` for GM memberships and may be `null` for unbound player memberships.
- Response must not expose password hashes, session token hashes, plaintext cookies, or internal API-key hashes.
- Recommended response header: `Cache-Control: no-store`.

## 2. First-run setup impact

### `POST /services/jdr/auth/setup`

Existing request body and success body remain unchanged from feature 003.

New BD-4 effect:

- When setup creates the first GM, the system also creates or identifies the V1 default campaign.
- The first GM is added as a campaign member with role `gm`.
- The first GM's default campaign points to that campaign.

The client must not send a campaign id.

## 3. User management impact

### `POST /services/jdr/users`

Existing request body remains unchanged:

```json
{
  "username": "bob",
  "profile": "user",
  "password": "string"
}
```

New BD-4 effect:

- The created user is added to the creator's active campaign when available.
- If no creator active campaign is available in V1, the user is added to the default campaign.
- `profile: "gm"` maps to campaign role `gm`.
- `profile: "user"` maps to campaign role `player`.

Success response remains the public user representation from feature 003. No membership list is exposed here.

### `GET /services/jdr/users`

Existing response shape remains unchanged.

New BD-4 effect:

- Results are filtered to users who are members of the authenticated user's active campaign.
- In V1, this normally returns all users because all users belong to the default campaign.

### `PATCH /services/jdr/users/{user_id}` and `DELETE /services/jdr/users/{user_id}`

Existing request/response behavior remains unchanged.

New BD-4 effect:

- Operations are allowed only for users in the authenticated user's active campaign.
- Logical deletion preserves campaign membership rows for traceability.

## 4. JDR data endpoint impact

No public request body gains a `campaign_id`.

The following existing endpoint families derive campaign scope server-side:

| Endpoint family | BD-4 scoping rule |
|---|---|
| `/services/jdr/sessions` | Create/list/detail/update/delete only within active campaign |
| `/services/jdr/sessions/{id}/audio` | Session must belong to active campaign |
| `/services/jdr/sessions/{id}/transcription*` | Session must belong to active campaign |
| `/services/jdr/sessions/{id}/chunks` | Session must belong to active campaign |
| `/services/jdr/sessions/{id}/mapping` | Session and mapped PJs must belong to active campaign |
| `/services/jdr/sessions/{id}/players` | Session and PJs must belong to active campaign |
| `/services/jdr/sessions/{id}/artifacts/*` | Session must belong to active campaign |
| `/services/jdr/pjs` | Create/list/detail/update/delete only within active campaign |
| `/services/jdr/jobs/{id}` | Job's session must belong to active campaign |
| `/services/jdr/me*` | Player membership/key must resolve to data inside the same campaign |

If a resource exists outside the active campaign, the endpoint should behave as not found or forbidden according to the existing endpoint's ownership semantics. It must not reveal cross-campaign existence.

## 5. Out of scope endpoints

BD-4 does not add:

- `POST /services/jdr/campaigns`
- `GET /services/jdr/campaigns`
- `PATCH /services/jdr/campaigns/{id}`
- `DELETE /services/jdr/campaigns/{id}`
- campaign switching endpoint
- tenant or organization endpoints
