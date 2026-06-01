# Data Model: Campaigns CRUD and Session Campaign Filter

BD-6 mostly exposes and tightens existing BD-4 entities. The implementation should first verify current schema fields before adding migrations.

## Entity Relationship Overview

```text
core_users
  1 |---- N jdr_campaigns as owner
  1 |---- N jdr_campaign_members
  ? |---- 1 jdr_campaigns as default_campaign

jdr_campaigns
  1 |---- N jdr_campaign_members
  1 |---- N jdr_sessions

jdr_sessions
  N |---- 1 jdr_campaigns
  N |---- 1 jdr_api_keys as GM owner

jdr_pjs
  N |---- 1 jdr_api_keys as owner
  # BD-6 public behavior: global to user/GM, not filtered by selected campaign
```

## Campaign

Represents a JDR campaign container.

### Fields

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `id` | UUID | yes | Stable campaign identifier |
| `name` | string | yes | 1-200 chars for BD-6 public contract |
| `description` | string/null | no | Max 4000 chars; add schema/storage support if absent |
| `owner_user_id` | UUID | yes | User who created or owns the campaign |
| `created_at` | datetime | yes | Serialized with explicit timezone |

### Derived response fields

| Field | Type | Source |
|-------|------|--------|
| `role` | `gm` or `player` | Current user's membership |
| `session_count` | integer | Count of sessions linked to campaign |
| `last_session_at` | datetime/null | Latest `recorded_at` among linked sessions |

### Validation and invariants

- Campaign name is required and cannot be blank.
- Campaign description is optional.
- Duplicate names for the same user should be rejected if feasible with current schema/query support.
- A campaign with sessions cannot be deleted in BD-6.

## Campaign Membership

Represents a user's role in a campaign.

### Fields

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `user_id` | UUID | yes | Composite identity with `campaign_id` |
| `campaign_id` | UUID | yes | Composite identity with `user_id` |
| `role` | enum | yes | `gm` or `player` |
| `character_id` | UUID/null | no | Existing BD-4 optional binding |
| `joined_at` | datetime | yes | Used for fallback active campaign ordering |

### Validation and invariants

- Read access requires a membership row.
- Campaign updates, deletes, and session creation require `role = gm`.
- Player memberships can view campaigns and campaign-filtered sessions when allowed by current product rules, but cannot mutate campaigns.

## Session

Represents a recorded JDR session.

### BD-6 field focus

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `campaign_id` | UUID | yes for new BD-6 sessions | Existing nullable rows must be backfilled |
| `recorded_at` | datetime | yes | Used for `last_session_at` aggregate |

### Validation and invariants

- New session creation requires `campaign_id`.
- The current user must be GM of the requested campaign to create a session.
- Session listing may be filtered by `campaign_id`; if filtered, all returned sessions must match it.
- Existing unfiltered listing remains available for backward compatibility.
- Fetching one session must check membership against the session's campaign.

## User

Represents the authenticated web account.

### BD-6 field focus

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `default_campaign_id` | UUID/null | no | Used for legacy session backfill and active campaign fallback |

### Validation and invariants

- Existing sessions with no campaign are assigned to the creator's default campaign during rollout.
- Users without memberships receive an empty campaign list, not leaked campaign data.

## Player Character

Represents a playable character.

### BD-6 rule

PJs remain global to the user/GM for BD-6. Public PJ listing and creation must not require a selected campaign. Existing session participation remains the link between a PJ and a campaign through the session.

## State Transitions

### Campaign lifecycle

```text
created -> updated -> deleted
created -> blocked_delete_due_to_sessions
```

- `deleted` is allowed only when `session_count = 0`.
- `blocked_delete_due_to_sessions` returns a conflict and leaves data unchanged.

### Legacy session rollout

```text
legacy_session_without_campaign -> assigned_to_creator_default_campaign
```

- Rollout must be idempotent.
- Sessions already assigned to campaigns remain unchanged.
