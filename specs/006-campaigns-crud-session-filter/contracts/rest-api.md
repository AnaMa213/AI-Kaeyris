# REST API Contract: Campaigns CRUD and Session Campaign Filter

Base path: `/services/jdr`

All endpoints below require authenticated web session access unless noted otherwise. Error responses follow the platform Problem Details contract.

## Shared Schemas

### `CampaignCreate`

```json
{
  "name": "Les Royaumes Brises",
  "description": "Un royaume autrefois uni se dechire..."
}
```

Rules:

- `name`: required, 1-200 characters.
- `description`: optional, max 4000 characters.

### `CampaignPatch`

```json
{
  "name": "Les Royaumes Brises (Tome II)",
  "description": "Suite de la premiere campagne."
}
```

Rules:

- Both fields are optional.
- If present, `name` follows create validation.
- If present, `description` follows create validation and may be null.

### `CampaignOut`

```json
{
  "id": "11111111-1111-1111-1111-111111111111",
  "name": "Les Royaumes Brises",
  "description": "Un royaume autrefois uni se dechire...",
  "role": "gm",
  "session_count": 12,
  "last_session_at": "2026-05-29T18:30:00Z",
  "created_at": "2026-01-12T18:00:00Z"
}
```

Rules:

- `role` is the current user's campaign role: `gm` or `player`.
- `session_count` is zero or greater.
- `last_session_at` is null when the campaign has no sessions.
- Date fields include an explicit timezone suffix.

### `Page_CampaignOut_`

```json
{
  "items": [],
  "total": 0,
  "page": 1,
  "size": 50
}
```

## Campaign Endpoints

### `GET /services/jdr/campaigns`

Lists campaigns where the current user is a member.

Query:

- No search/sort required for BD-6.
- Default page shape: `page = 1`, `size = 50`.

Success `200`:

```json
{
  "items": [
    {
      "id": "11111111-1111-1111-1111-111111111111",
      "name": "Les Royaumes Brises",
      "description": null,
      "role": "gm",
      "session_count": 0,
      "last_session_at": null,
      "created_at": "2026-01-12T18:00:00Z"
    }
  ],
  "total": 1,
  "page": 1,
  "size": 50
}
```

### `POST /services/jdr/campaigns`

Creates a campaign and makes the creator GM of it.

Body: `CampaignCreate`

Success `201`: `CampaignOut` with:

- `role = "gm"`
- `session_count = 0`
- `last_session_at = null`

Errors:

- `422` invalid name or description.
- `409` duplicate campaign name for the current user.

### `GET /services/jdr/campaigns/{campaign_id}`

Fetches one campaign.

Success `200`: `CampaignOut`

Errors:

- `404` campaign does not exist.
- `403` current user is not a member.

### `PATCH /services/jdr/campaigns/{campaign_id}`

Updates name and/or description.

Body: `CampaignPatch`

Success `200`: updated `CampaignOut`

Errors:

- `403` current user is not GM of the campaign.
- `404` campaign does not exist.
- `422` invalid patch body.
- `409` duplicate campaign name for the current user.

### `DELETE /services/jdr/campaigns/{campaign_id}`

Deletes an empty campaign.

Success:

- `204 No Content`

Errors:

- `403` current user is not GM of the campaign.
- `404` campaign does not exist.
- `409` campaign has one or more sessions and cannot be deleted.

## Session Contract Changes

### `GET /services/jdr/sessions?campaign_id={uuid}`

Adds optional campaign filtering.

Rules:

- If `campaign_id` is present, current user must be a member of that campaign.
- Returned sessions must all belong to that campaign.
- If `campaign_id` is absent, existing unfiltered behavior is preserved for backward compatibility.

Success `200`: existing page of `SessionOut`.

Errors:

- `403` current user is not a member of requested campaign.
- `422` invalid campaign id format.

### `POST /services/jdr/sessions`

Adds required `campaign_id`.

Body:

```json
{
  "title": "Session 13 - La crypte oubliee",
  "recorded_at": "2026-05-31T18:00:00Z",
  "transcription_mode": "non_diarised",
  "campaign_id": "11111111-1111-1111-1111-111111111111",
  "campaign_context": "Optional campaign bible block."
}
```

Rules:

- `campaign_id` is required for BD-6.
- Current user must be GM of the campaign.
- Existing datetime input compatibility from BD-5 remains.

Success `201`: existing `SessionOut`.

Errors:

- `403` current user is not GM of requested campaign.
- `422` missing or invalid `campaign_id`.

### `GET /services/jdr/sessions/{session_id}`

No path or response shape change.

Rule:

- Current user must be a member of the session's campaign.

Errors:

- `403` current user is not a member of the campaign.
- `404` session does not exist.

## OpenAPI Expectations

The public machine-readable contract includes:

- 5 campaign endpoints.
- `CampaignCreate`, `CampaignPatch`, `CampaignOut`, and campaign page schema.
- `campaign_id` query parameter on session list.
- required `campaign_id` body field on session create.
