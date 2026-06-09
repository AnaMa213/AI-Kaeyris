# Data Model: BD-12 PJ Update

## Existing Entity: Player Character (`Pj`)

**Table**: `jdr_pjs`

| Field | Type | Update Behavior |
|---|---|---|
| `id` | UUID | Immutable path identifier |
| `name` | string, 1..255 | Editable when present in `PjUpdate` |
| `owner_gm_key_id` | UUID FK to `jdr_api_keys.id` | Immutable ownership boundary |
| `campaign_id` | UUID FK to `jdr_campaigns.id` | Immutable in BD-12 |
| `user_id` | nullable UUID FK to `core_users.id` | Editable when present; `null` unlinks |
| `created_at` | datetime | Immutable |

## New API Schema: `PjUpdate`

Partial update request for `PATCH /services/jdr/pjs/{pj_id}`.

| Field | Type | Required | Validation | Semantics |
|---|---|---:|---|---|
| `name` | string | No | min length 1, max length 255 | Rename PJ when present |
| `user_id` | UUID or null | No | UUID must reference an existing user when not null | Link to user; explicit null clears link |

## Existing API Schema: `PjOut`

Response remains unchanged:

| Field | Type |
|---|---|
| `id` | UUID |
| `name` | string |
| `campaign_id` | UUID |
| `user_id` | UUID or null |
| `created_at` | datetime |

## Validation Rules

- A caller can update only a PJ owned by the current GM/campaign scope.
- `name` must satisfy the same validation as `PjCreate.name`.
- `name` must remain unique in the existing `(owner_gm_key_id, name)` scope.
- `user_id` may be omitted, a valid existing user UUID, or explicit `null`.
- Unknown non-null `user_id` returns `422 invalid-user`; if PJ creation currently
  differs, BD-12 should align creation and update to the same category.
- Empty JSON `{}` is accepted as a no-op and returns the current `PjOut`.

## State Transitions

```text
Existing PJ
  + PATCH {"name": "..."}          -> same PJ id, new name
  + PATCH {"user_id": "<uuid>"}    -> same PJ id, linked user
  + PATCH {"user_id": null}        -> same PJ id, no linked user
  + PATCH {"name": "...", "user_id": ...}
                                  -> same PJ id, both editable fields updated
```

No deletion, campaign move, owner change, or history tracking is introduced.
