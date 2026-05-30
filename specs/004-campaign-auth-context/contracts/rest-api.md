# Contracts: REST API - Campaign Auth Context

**Phase 1 du `/speckit-plan`**. Surface REST ajoutee ou comportement modifie par BD-4.

All protected-route errors keep the existing platform Problem Details format unless an endpoint states otherwise. `/auth/me` is the only new public contract surface in this feature.

## 1. Current authenticated user context

### `GET /services/jdr/auth/me`

Requires a valid browser session cookie. No request body. No query parameter.

**Success with active campaign: 200**

Headers:

```http
Cache-Control: no-store
```

Body:

```json
{
  "user": {
    "id": "11111111-2222-3333-4444-555555555555",
    "username": "kenan"
  },
  "active_campaign": {
    "id": "00000000-0000-0000-0000-000000000001",
    "name": "Campagne par defaut",
    "role": "mj",
    "character_id": null
  }
}
```

**Success without campaign context: 200**

```json
{
  "user": {
    "id": "11111111-2222-3333-4444-555555555555",
    "username": "kenan"
  },
  "active_campaign": null
}
```

**Unauthenticated: 401**

Existing protected-route Problem Details response. The frontend interceptor treats this like an expired/invalid session.

```json
{
  "type": "https://errors.ai-kaeyris.local/unauthorized",
  "title": "Unauthorized",
  "status": 401,
  "detail": "Missing or malformed credentials."
}
```

## 2. User management behavior

The request/response bodies from feature 003 remain unchanged.

### `POST /services/jdr/users`

Additional BD-4 effects:

- Creates a normal user as before.
- Adds the new user to the current active/default campaign.
- Derives membership role:
  - `profile = "gm"` -> `role = "mj"`
  - `profile = "user"` -> `role = "player"`
- Does not accept `campaign_id` in the request body. If present, validation rejects the request.

### `GET /services/jdr/users`

Additional BD-4 behavior:

- Returns users that are members of the active campaign.
- Does not expose membership internals unless a future contract explicitly adds them.

### `PATCH /services/jdr/users/{user_id}`

Additional BD-4 behavior:

- Existing user update contract remains stable.
- If `profile` changes, the corresponding active/default campaign membership role must stay consistent for V1 (`gm -> mj`, `user -> player`).

### `DELETE /services/jdr/users/{user_id}`

Additional BD-4 behavior:

- Keeps logical deletion as before.
- Keeps `campaign_members` rows for auditability.

## 3. JDR data scoping behavior

All JDR data endpoints derive the campaign from the authenticated context. Frontend request bodies must not send `campaign_id`; explicit `campaign_id` fields in create/update bodies are rejected with validation error semantics.

### Session endpoints

Affected endpoint families:

- `POST /services/jdr/sessions`
- `GET /services/jdr/sessions`
- `GET /services/jdr/sessions/{session_id}`
- `PATCH /services/jdr/sessions/{session_id}`
- all session child endpoints such as audio, chunks, mapping, players, transcription and artifacts
- player-facing session endpoints under `/services/jdr/me/*`
- `GET /services/jdr/jobs/{job_id}`

Contract additions:

- Creation assigns `campaign_id = active_campaign.id` server-side.
- Lists return only rows for `active_campaign.id`.
- Single-resource operations require the resource to belong to `active_campaign.id`; otherwise they return the existing not-found/forbidden convention for that endpoint.
- Player-facing `/me/*` endpoints and job status reads inherit campaign scope from their session/PJ and must not reveal foreign-campaign data.

### PJ/character endpoints

Affected endpoint families:

- `POST /services/jdr/pjs`
- `GET /services/jdr/pjs`
- routes that validate PJ ownership for mappings, session players, player enrollment, and POVs

Contract additions:

- Creation assigns `campaign_id = active_campaign.id` server-side.
- Lists and validations are limited to PJs in `active_campaign.id`.

## 4. Out of scope REST surfaces

These endpoints must not be added in BD-4:

- `POST /services/jdr/campaigns`
- `GET /services/jdr/campaigns`
- `PATCH /services/jdr/campaigns/{campaign_id}`
- `PATCH /services/jdr/users/{id}/default_campaign_id`
- endpoints listing all memberships for a user
- tenant/organization endpoints

## 5. OpenAPI requirement

The backend runtime OpenAPI output at `/openapi.json` must include `GET /services/jdr/auth/me` and its response schema so the frontend can regenerate client types from the backend contract. A generated file such as `docs/context/api/openapi.json` is refreshed only if the backend repo already versions that artifact.
