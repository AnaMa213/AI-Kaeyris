# Data Model: Campaign Auth Context

**Phase 1 du `/speckit-plan`**. Target model for BD-4 campaign context, memberships, `/auth/me`, and campaign-scoped JDR data.

## 1. Schema overview

```text
core_users
  1 |---- N core_web_sessions
  N |---- N jdr_campaigns via jdr_campaign_members
  ? |---- 1 jdr_campaigns as default_campaign

jdr_campaigns
  1 |---- N jdr_campaign_members
  1 |---- N jdr_sessions
  1 |---- N jdr_pjs

jdr_sessions
  1 |---- children already present: audio_source, transcription, mappings,
          chunks, session_players, artifacts, jobs
```

Campaign is the JDR visibility boundary. V1 product usage has one default campaign, but the membership table allows a later user to be GM in one campaign and player in another.

## 2. `jdr_campaigns`

JDR campaign container.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | UUID | yes | Primary key |
| `name` | string(255) | yes | V1 default: `Campagne par defaut` |
| `owner_user_id` | UUID | yes | FK to `core_users.id`; oldest active GM during backfill |
| `created_at` | datetime tz | yes | Creation timestamp |

### Constraints

- `name` must not be blank.
- `owner_user_id` must point to an existing user.
- V1 adoption creates at most one default campaign automatically.

## 3. `jdr_campaign_members`

Membership between a web user and a JDR campaign.

| Field | Type | Required | Notes |
|---|---|---|---|
| `user_id` | UUID | yes | FK to `core_users.id` |
| `campaign_id` | UUID | yes | FK to `jdr_campaigns.id` |
| `role` | enum/string | yes | `gm` or `player` |
| `character_id` | UUID | no | FK to `jdr_pjs.id`; null for GM and for unbound players |
| `joined_at` | datetime tz | yes | Membership creation timestamp |

Primary key: `(user_id, campaign_id)`.

### Constraints

- `role` accepted values: `gm`, `player`.
- A player membership may have `character_id = null` until a binding exists; the system must never invent a character.
- If `character_id` is set, the PJ must belong to the same campaign.
- Logical user deletion keeps membership rows for traceability, but deleted users cannot authenticate.

### Profile to membership role mapping

| Existing profile | Membership role |
|---|---|
| `gm` | `gm` |
| `user` | `player` |

## 4. `core_users.default_campaign_id`

Optional pointer to the user's preferred campaign.

| Field | Type | Required | Notes |
|---|---|---|---|
| `default_campaign_id` | UUID | no | FK to `jdr_campaigns.id`; used by `/auth/me` if membership is valid |

### Resolution rules

```text
if user.default_campaign_id points to a valid membership:
    active_campaign = that membership
elif user has any valid membership:
    active_campaign = first by joined_at, then campaign_id
else:
    active_campaign = null
```

Invalid defaults are ignored rather than returned.

## 5. `jdr_sessions.campaign_id`

Add campaign scope to JDR sessions.

| Field | Type | Required | Notes |
|---|---|---|---|
| `campaign_id` | UUID | yes after migration | FK to `jdr_campaigns.id` |

### Rules

- New sessions derive `campaign_id` from authenticated campaign scope.
- Existing sessions are backfilled to the V1 default campaign.
- Session list/detail/update/delete and artefact workflows filter by `campaign_id`.
- The request body must not accept or require `campaign_id`.

## 6. `jdr_pjs.campaign_id`

Add campaign scope to JDR player characters.

| Field | Type | Required | Notes |
|---|---|---|---|
| `campaign_id` | UUID | yes after migration | FK to `jdr_campaigns.id` |

### Rules

- New PJs derive `campaign_id` from authenticated campaign scope.
- Existing PJs are backfilled to the V1 default campaign.
- PJ list/detail/update/delete and mapping/player validation filter by `campaign_id`.
- Existing owner GM behavior remains in V1; campaign scope is an additional boundary.

## 7. Derived child scope

These tables do not need a duplicated `campaign_id` in V1 because their scope is derived:

| Table | Scope source |
|---|---|
| `jdr_audio_sources` | owning `jdr_sessions.campaign_id` |
| `jdr_transcriptions` | owning `jdr_sessions.campaign_id` |
| `jdr_session_pj_mappings` | owning `jdr_sessions.campaign_id` and mapped `jdr_pjs.campaign_id` |
| `jdr_chunks` | owning `jdr_sessions.campaign_id` |
| `jdr_session_players` | owning `jdr_sessions.campaign_id` and `jdr_pjs.campaign_id` |
| `jdr_artifacts` | owning `jdr_sessions.campaign_id` |
| `jdr_jobs` | owning `jdr_sessions.campaign_id` |

## 8. Current-context response model

Public response of `GET /services/jdr/auth/me`.

```json
{
  "user": {
    "id": "uuid",
    "username": "kenan"
  },
  "active_campaign": {
    "id": "uuid",
    "name": "Campagne par defaut",
    "role": "gm",
    "character_id": null
  }
}
```

`active_campaign` may be `null`.

### Constraints

- No password hash.
- No session token or token hash.
- No internal API-key hash.
- No unrelated memberships.
- Role is `gm` or `player`.

## 9. Adoption/backfill states

| State | Meaning | Required action |
|---|---|---|
| Empty DB | No users exist | First-run setup creates first GM, default campaign, and GM membership atomically |
| Existing users, no campaigns | Pre-BD-4 deployment | Migration/adoption creates default campaign, memberships, and backfills session/PJ campaign ids |
| Existing campaigns | Re-run/idempotent adoption | Do not duplicate default campaign or memberships |

## 10. Invariants

- Every active web user created through JDR user management has at least one campaign membership.
- Every new JDR session and PJ has a campaign id derived server-side.
- A user can only receive an active campaign where a membership exists.
- Campaign scope is never widened by request body, query string, or path input.
- Deleted/inactive users cannot authenticate even if memberships still exist.
