# Research: Identity Refactor and PJ Campaign Scoping

## Decision 1: Treat BD-7 as purge/reseed for impacted local/staging data

**Decision**: Implement a schema migration that may drop/recreate or otherwise rebuild impacted identity/JDR structures without preserving existing local/staging rows.

**Rationale**: The handoff explicitly states "Migration de données: NONE — purge" and confirms the owner accepts data loss for existing local/staging data. This avoids a complex compatibility migration while the product is still in local/staging development.

**Alternatives considered**:

- Preserve and transform existing rows: rejected because the owner accepted purge and the old model conflates concepts that are easier to correct from a clean state.
- Keep both old and new columns temporarily: rejected as unnecessary transitional complexity for the accepted purge scope.

## Decision 2: Separate global account role from campaign role

**Decision**: Replace public account `profile` semantics with `system_role: admin | user`, and reserve campaign authority for `CampaignMember.role`.

**Rationale**: BD-7's primary problem is that "can administer the portal" and "is GM of a campaign" are independent dimensions. Keeping a global `gm` profile would continue the conflation and make later frontend authorization ambiguous.

**Alternatives considered**:

- Keep `profile: gm | user` and add separate admin flag: rejected because public vocabulary would remain confusing and would still require frontend special casing.
- Rename `profile` to `role`: rejected because "role" is already meaningful at campaign membership level.

## Decision 3: Rename campaign member `player` to `pj`

**Decision**: Campaign membership roles become `gm | pj` in public data and newly seeded data.

**Rationale**: The frontend and product vocabulary use the French JDR concept "PJ"; aligning the backend contract removes translation ambiguity in `/auth/me`, campaign memberships, and frontend role checks.

**Alternatives considered**:

- Keep `player` internally but map to `pj` externally: rejected because it preserves two names for the same domain concept and increases test/serialization risk.
- Use `user` as the campaign role: rejected because it conflicts with the global `system_role: user`.

## Decision 4: Keep legacy API-key player role separate from web campaign `pj`

**Decision**: BD-7 plans the web/campaign role rename to `pj`, while legacy API-key `Role.PLAYER` remains a separate machine-token concept unless an implementation task proves it can be safely renamed without breaking player-token endpoints.

**Rationale**: Existing routes and tests use DB-backed API keys for player-token access to `/me/*`. The handoff targets `campaign_members.role`, not necessarily all API-key role enum values. Separating these concepts reduces blast radius.

**Alternatives considered**:

- Rename every occurrence of "player" in one pass: rejected as broader than BD-7 and risky because historical player-token endpoints still exist.
- Leave campaign membership as `player`: rejected because AC2/AC6 require public `pj`.

## Decision 5: Make PJs campaign-scoped and optionally user-assigned

**Decision**: Every PJ must have a campaign, and may have an assigned user account.

**Rationale**: BD-6 intentionally kept PJs global for compatibility, but BD-7 is the handoff that resolves this model debt. Optional user assignment supports creating characters before assigning them to a player.

**Alternatives considered**:

- Keep PJs global and filter by owner only: rejected because it leaks characters across campaigns and blocks future campaign-specific PJ screens.
- Require `user_id` at PJ creation: rejected because GMs often create party rosters before players are invited or assigned.

## Decision 6: Use V1 fallback for `POST /pjs` without `campaign_id`

**Decision**: `campaign_id` is optional in PJ creation during BD-7; if omitted, the backend assigns the PJ to the current user's default campaign.

**Rationale**: The handoff recommends Option A to avoid breaking already-delivered frontend PJ Stories 2.1/2.2. This is a deliberate compatibility bridge, not the final scoped UI shape.

**Alternatives considered**:

- Require `campaign_id` immediately: rejected because it would break delivered frontend POST `/pjs` flows.
- Keep PJs global indefinitely: rejected because BD-7's purpose is to scope them by campaign.

## Decision 7: Make `/services/jdr/users/*` admin-only

**Decision**: User account management requires `system_role: admin`; being GM of a campaign is not enough.

**Rationale**: This is the behavioral heart of identity separation. A GM can manage their campaign content but not the portal's user directory.

**Alternatives considered**:

- Allow all campaign GMs to manage users: rejected because it recreates the current conflation.
- Allow only first seeded user forever: rejected because it prevents future admin delegation.

## Decision 8: Seed behavior is local/staging explicit, not production hardcoded

**Decision**: BD-7 may include a documented reseed path for one admin, one default campaign, and GM membership, but production credentials must not be hardcoded or silently enabled.

**Rationale**: The handoff asks for a reseeded admin after purge, while the project constitution forbids committed credentials. The safe compromise is explicit local/staging setup, test fixtures, or configuration-driven seeding.

**Alternatives considered**:

- Commit `admin/admin` as universal default credentials: rejected as a direct security violation.
- Require manual SQL after every purge: rejected because it is error-prone and undermines reproducibility.
