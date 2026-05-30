# Data Model: Campaign Auth Context

**Phase 1 du `/speckit-plan`**. Modele cible pour campagnes, memberships, contexte utilisateur courant et scoping des donnees JDR.

## 1. Schema overview

```text
core_users
  N |---- N campaigns via campaign_members
  1 |---- 0..1 campaigns via default_campaign_id

campaigns
  1 |---- N campaign_members
  1 |---- N jdr_sessions
  1 |---- N jdr_pjs

jdr_sessions
  1 |---- N child data: audio, transcription, chunks, mappings, artifacts, jobs, session_players
```

`campaigns` is the tenancy root. `campaign_members` is the role-bearing join table. Existing JDR child tables inherit campaign scope through their parent session/PJ unless the implementation needs a direct index for a root list.

## 2. `campaigns`

JDR play space and multi-tenancy boundary.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | UUID | yes | Primary key. V1 seed uses fixed id `00000000-0000-0000-0000-000000000001`. |
| `name` | string/text | yes | Display name, e.g. `Campagne par defaut`. |
| `owner_id` | UUID | yes | FK to `core_users.id`; V1 owner is the first/primary GM user. |
| `created_at` | datetime tz | yes | Creation timestamp. |

### Constraints

- `name` must not be blank.
- `owner_id` must reference an existing user.
- V1 seed creates exactly one default campaign for normal local usage.

## 3. `campaign_members`

Membership and role of a user inside one campaign.

| Field | Type | Required | Notes |
|---|---|---|---|
| `user_id` | UUID | yes | FK to `core_users.id`. |
| `campaign_id` | UUID | yes | FK to `campaigns.id`. |
| `role` | enum/string | yes | `mj` or `player`. |
| `character_id` | UUID | no | Optional FK to `jdr_pjs.id`; nullable for MJ. |
| `joined_at` | datetime tz | yes | Used for deterministic fallback. |

### Constraints

- Primary key: `(user_id, campaign_id)`.
- `role` accepted values: `mj`, `player`.
- A user can have multiple campaign memberships in the data model, but V1 creates one membership per user.
- `character_id` may be null. If non-null, it must reference a PJ/character in the same campaign.

## 4. `core_users` additions

Existing browser user account gains a default campaign reference.

| Field | Type | Required | Notes |
|---|---|---|---|
| `default_campaign_id` | UUID | no | FK to `campaigns.id`; used first during active campaign resolution. |

### Rules

- Existing `profile` remains in V1 for compatibility.
- During migration/seed, all existing users get `default_campaign_id` set to the V1 campaign.
- New users created through `/services/jdr/users` get a membership in the active/default campaign and should receive that campaign as their default unless a stricter future rule says otherwise.

## 5. `jdr_sessions` additions

Root aggregate for uploaded/processed JDR session data.

| Field | Type | Required | Notes |
|---|---|---|---|
| `campaign_id` | UUID | yes after backfill | FK to `campaigns.id`; automatically assigned on create. |

### Rules

- `POST /services/jdr/sessions` must not accept `campaign_id` from the frontend.
- `GET /services/jdr/sessions` filters by active campaign.
- Single-session reads and updates require the row to belong to the active campaign.
- Existing rows are backfilled to the V1 default campaign.

## 6. `jdr_pjs` additions

Root aggregate for characters/Personnages Joueurs.

| Field | Type | Required | Notes |
|---|---|---|---|
| `campaign_id` | UUID | yes after backfill | FK to `campaigns.id`; automatically assigned on create. |

### Rules

- `GET /services/jdr/pjs` filters by active campaign.
- Existing uniqueness by owner/name may need to become campaign-aware if one GM can later own the same PJ name in different campaigns.
- `campaign_members.character_id`, mappings and session-player lists must reference PJs in the same active campaign.

## 7. Active campaign context

Derived object, not necessarily a separate table.

| Field | Source | Notes |
|---|---|---|
| `user.id` | `core_users.id` | Authenticated web user. |
| `user.username` | `core_users.username` | Public display/login identifier. |
| `active_campaign.id` | `campaigns.id` | Null when no campaign context exists. |
| `active_campaign.name` | `campaigns.name` | Display name. |
| `active_campaign.role` | `campaign_members.role` | `mj` or `player`. |
| `active_campaign.character_id` | `campaign_members.character_id` | Nullable. |

### Resolution algorithm

```text
if user.default_campaign_id points to one of user's memberships:
    use that membership
else if user has any membership:
    use earliest membership by joined_at
else:
    active_campaign = null
```

## 8. Migration/backfill rules

1. Create campaign and membership tables.
2. Add nullable `core_users.default_campaign_id`.
3. Create the V1 default campaign if users exist or seed requires it.
4. Insert one membership per existing user:
   - `profile = gm` -> `role = mj`
   - `profile = user` -> `role = player`
5. Set `core_users.default_campaign_id` for all existing users.
6. Add nullable `campaign_id` to campaign-owned JDR root tables.
7. Backfill existing JDR rows to the V1 campaign.
8. Make required `campaign_id` columns non-null when the database supports it safely, or enforce non-null at app level for SQLite-compatible migrations if needed.

## 9. State transitions

### Membership lifecycle

```text
created --> retained
```

No V1 deletion/update endpoint exists. A soft-deleted user keeps membership rows for auditability.

### Active campaign state

```text
no_membership --> active_campaign:null
membership_exists --> active_campaign resolved
default_changed_later --> active_campaign follows default if membership valid
```

Default changing is out of scope for V1, but the model is compatible with it later.
