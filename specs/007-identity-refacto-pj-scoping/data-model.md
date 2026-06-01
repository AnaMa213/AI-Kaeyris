# Data Model: Identity Refactor and PJ Campaign Scoping

## Overview

BD-7 separates global account permissions from campaign membership permissions and makes PJs campaign-owned. The model deliberately preserves legacy API-key concepts where they are still used for player-token access, but web-facing account/campaign identity uses the new vocabulary.

## Entities

### User Account

Represents a person who can sign in to the portal.

**Fields**:

- `id`: stable unique identifier.
- `username`: unique login/display identifier.
- `password_hash`: non-public credential hash.
- `system_role`: global account role, one of `admin` or `user`.
- `status`: account lifecycle status as currently supported.
- `api_key_id`: optional internal JDR API-key bridge for existing JDR ownership references.
- `default_campaign_id`: optional default campaign used for active-campaign and V1 PJ fallback behavior.
- `created_at`, `updated_at`: public timestamps where exposed.

**Validation rules**:

- `username` is required and unique.
- `system_role` is required and must be `admin` or `user`.
- Public user inputs and outputs use `system_role`, not `profile`.
- Non-admin users cannot manage other accounts.

**Relationships**:

- May own an internal JDR API key.
- May belong to many campaigns through Campaign Membership.
- May have one default campaign.
- May be assigned to zero or more PJs, depending on future product rules.

### System Role

Represents global portal authority.

**Values**:

- `admin`: can manage user accounts.
- `user`: standard account; can create and participate in campaigns.

**Rules**:

- System role does not imply campaign GM authority.
- Campaign GM authority does not imply system admin authority.

### Campaign

Represents a JDR campaign container.

**Fields**:

- `id`: stable unique identifier.
- `name`: campaign display name.
- `description`: optional campaign description.
- `owner_user_id`: creator/owner reference.
- `created_at`: creation timestamp.

**Relationships**:

- Has many Campaign Memberships.
- Has many Sessions.
- Has many PJs.

**Rules**:

- Any authenticated user may create a campaign.
- The creator becomes campaign GM.
- Existing BD-6 delete guard remains: campaigns with sessions cannot be deleted.

### Campaign Membership

Represents a user's role in one campaign.

**Fields**:

- `user_id`: member user.
- `campaign_id`: campaign.
- `role`: one of `gm` or `pj`.
- `character_id`: optional PJ association for PJ members.
- `joined_at`: membership timestamp.

**Validation rules**:

- `(user_id, campaign_id)` is unique.
- `role` must not expose or store `player` after BD-7 purge/reseed.
- If `character_id` is set, the referenced PJ must belong to the same campaign.

**Authorization rules**:

- `gm` members may mutate campaign-owned resources such as sessions and PJs.
- `pj` members may read campaign/PJ data where the endpoint allows member access.

### PJ

Represents a playable character scoped to one campaign.

**Fields**:

- `id`: stable unique identifier.
- `name`: character display name.
- `owner_gm_key_id`: existing ownership bridge for JDR GM records.
- `campaign_id`: required campaign reference.
- `user_id`: optional assigned user account.
- `created_at`: creation timestamp.

**Validation rules**:

- `name` is required.
- `campaign_id` is required after BD-7.
- `user_id` may be null.
- Creating a PJ requires GM membership in the resolved campaign.
- If `campaign_id` is omitted from V1 create input, the creator's default campaign is used.
- If no explicit or default campaign can be resolved, creation fails.

**Relationships**:

- Belongs to exactly one Campaign.
- May be assigned to one User Account.
- May be referenced by Campaign Membership as a member's active character.
- May be used by session mapping/player-list features.

### Current Identity

Frontend-safe view of the signed-in user.

**Fields**:

- `user.id`
- `user.username`
- `user.system_role`
- `active_campaign.id`
- `active_campaign.name`
- `active_campaign.role`
- `active_campaign.character_id`

**Rules**:

- `user.system_role` is always present for authenticated web sessions.
- `active_campaign` may be null when no campaign membership exists.
- `active_campaign.role` is `gm` or `pj`, never `player`.

## State and Data Transitions

### Clean purge/reseed

1. Impacted local/staging identity and JDR tables are purged or rebuilt.
2. Schema is recreated with `system_role`, campaign member role `gm | pj`, required PJ campaign, and optional PJ user assignment.
3. A local/staging seed path creates:
   - one admin user,
   - one default campaign,
   - one GM membership for that admin,
   - default campaign assignment for the admin.

### User creates campaign

1. Authenticated user submits valid campaign data.
2. Campaign is created.
3. Membership is created with role `gm`.
4. Default campaign may be set if the user has none.

### GM creates PJ

1. GM submits PJ name and optional `campaign_id` / `user_id`.
2. System resolves campaign from explicit value or default campaign fallback.
3. System verifies the creator is GM of that campaign.
4. PJ is created with required campaign and optional user assignment.

### User removal and PJ assignment

If a user assigned to a PJ is removed, the PJ remains and becomes unassigned rather than being deleted.

## Migration Notes

- BD-7 does not require preserving existing local/staging identity/JDR rows.
- The migration should still be explicit and reversible enough for development rollback where practical.
- Production hardcoded seed credentials are not allowed; seeding must be explicit local/staging setup or configuration-driven.
