# Feature Specification: Identity Refactor and PJ Campaign Scoping

**Feature Branch**: `[007-identity-refacto-pj-scoping]`  
**Created**: 2026-06-01  
**Status**: Draft  
**Input**: User description: "BD-7 identity refacto and PJ scoping backend handoff"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Separate System Admin From Campaign GM (Priority: P1)

As a signed-in platform user, I need my global account permissions to be separate from my campaign role so that being the game master of a campaign does not automatically make me an administrator of the whole portal.

**Why this priority**: This fixes the core identity model before later campaign and PJ stories build on top of it.

**Independent Test**: Can be tested by creating one administrator account and one standard account, then verifying that only the administrator can manage accounts while both users can create and manage their own campaigns according to campaign membership.

**Acceptance Scenarios**:

1. **Given** a standard signed-in user, **When** they attempt to manage user accounts, **Then** the request is rejected with an admin-required forbidden response.
2. **Given** a standard signed-in user, **When** they create a campaign, **Then** the campaign is created and the user becomes GM of that campaign.
3. **Given** an administrator signed-in user, **When** they manage users, **Then** account management remains available.

---

### User Story 2 - Rename Campaign Player Role To PJ (Priority: P1)

As a JDR user, I need campaign membership roles to use the domain term "PJ" instead of the generic "player" so that backend and frontend vocabulary stay aligned with the product language.

**Why this priority**: The frontend identity context depends on consistent role values for navigation and authorization.

**Independent Test**: Can be tested by assigning a user to a campaign as a PJ and verifying every public identity and campaign membership response uses `pj`, never `player`.

**Acceptance Scenarios**:

1. **Given** a user is a PJ member of a campaign, **When** their current identity is fetched, **Then** the campaign role is reported as `pj`.
2. **Given** campaign membership data exists after the purge/reseed, **When** memberships are listed or resolved, **Then** no membership exposes the old `player` role.

---

### User Story 3 - Scope PJs To Campaigns (Priority: P1)

As a campaign GM, I need PJs to belong to a specific campaign so that character lists, assignments, and future PJ management do not leak across unrelated campaigns.

**Why this priority**: This is blocking for the next frontend PJ stories, because BD-6 intentionally left public PJ endpoints global as a temporary compatibility step.

**Independent Test**: Can be tested by creating PJs in two campaigns for the same user and verifying campaign filtering, authorization, and creation behavior.

**Acceptance Scenarios**:

1. **Given** a GM belongs to two campaigns, **When** they create a PJ for one campaign, **Then** the PJ is attached to that campaign.
2. **Given** a user is not a member of a campaign, **When** they request PJs for that campaign, **Then** access is rejected.
3. **Given** a GM lists PJs without choosing a campaign, **When** they belong to multiple campaigns, **Then** they see PJs from all campaigns where they are a member for V1 compatibility.
4. **Given** a GM creates a PJ without a campaign selection during the V1 compatibility window, **When** they have a default campaign, **Then** the PJ is attached to that default campaign.

---

### User Story 4 - Expose Current Identity For The Frontend (Priority: P1)

As the frontend app, I need the current identity response to include the user's global system role and the active campaign role using the new vocabulary so that screens can decide what to show without guessing.

**Why this priority**: Frontend Story 2.5+ depends on a reliable distinction between global admin actions and campaign-scoped GM/PJ actions.

**Independent Test**: Can be tested by signing in as an admin, a standard GM, and a PJ member, then checking that the returned identity contains the expected global and campaign roles.

**Acceptance Scenarios**:

1. **Given** a signed-in administrator, **When** current identity is fetched, **Then** the user object includes `system_role: admin`.
2. **Given** a signed-in standard user, **When** current identity is fetched, **Then** the user object includes `system_role: user`.
3. **Given** a signed-in PJ campaign member, **When** current identity is fetched, **Then** the active campaign role is `pj` and the character id is present when assigned.

---

### User Story 5 - Start From A Clean Reseed (Priority: P2)

As the project owner, I want BD-7 to allow purging local and staging identity/JDR data so the schema can be simplified without complex data migration.

**Why this priority**: The handoff explicitly accepts data loss for impacted local/staging tables, reducing migration risk and implementation cost.

**Independent Test**: Can be tested on an empty database by applying the new schema and verifying the seed creates one admin, one default campaign, and a GM membership.

**Acceptance Scenarios**:

1. **Given** the impacted data has been purged, **When** the backend is initialized, **Then** a known admin seed, default campaign, and GM membership can be created.
2. **Given** the reseeded admin signs in, **When** they fetch current identity, **Then** the response identifies them as `admin` and `gm` of the default campaign.

### Edge Cases

- A standard user tries to manage accounts: the action is rejected even if they are GM of one or more campaigns.
- A user creates a campaign while not being an administrator: the action succeeds and grants campaign GM membership.
- A PJ is created without a campaign during the V1 compatibility window and the user has no default campaign: the request is rejected with a clear validation or setup error.
- A user lists PJs without a campaign filter and belongs to no campaigns: the response is an empty list.
- A user requests PJs for a campaign where they are not a member: the response does not reveal PJ data.
- A PJ assigned to a user remains valid if the user exists; if that user is removed, the PJ becomes unassigned rather than deleted.
- Public responses must not expose the retired `profile` field or the retired campaign role value `player`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST replace the global account role concept currently named `profile` with `system_role`.
- **FR-002**: `system_role` MUST support exactly two public values: `admin` and `user`.
- **FR-003**: Only users with `system_role: admin` MUST be allowed to create, list, update, or delete user accounts.
- **FR-004**: Users with `system_role: user` MUST be allowed to create campaigns and become GM of campaigns they create.
- **FR-005**: Campaign membership role values MUST support `gm` and `pj`, and MUST NOT expose the retired `player` value in new data or public responses.
- **FR-006**: Current identity responses MUST include each signed-in user's `system_role`.
- **FR-007**: Current identity responses MUST expose active campaign role as `gm` or `pj`.
- **FR-008**: PJ records MUST always belong to exactly one campaign.
- **FR-009**: PJ records MAY be associated with one user account, and MUST also support being unassigned.
- **FR-010**: Creating a PJ MUST associate it with a campaign either from an explicit campaign selection or, during the V1 compatibility window, from the creator's default campaign.
- **FR-011**: Creating a PJ MUST require the creator to be GM of the selected or default campaign.
- **FR-012**: Listing PJs without a campaign filter MUST return PJs from campaigns where the signed-in user is a member.
- **FR-013**: Listing PJs with a campaign filter MUST require membership in that campaign.
- **FR-014**: Campaign-scoped session actions MUST continue to authorize by the session's campaign membership and GM role where mutation is required.
- **FR-015**: Account management forbidden responses MUST clearly indicate that administrator privileges are required.
- **FR-016**: Public user create and update inputs MUST use `system_role`, not `profile`.
- **FR-017**: Public PJ outputs MUST include campaign identity and optional assigned user identity.
- **FR-018**: The feature MUST support a clean purge/reseed path for impacted local/staging identity and JDR tables.
- **FR-019**: The reseed path MUST create one administrator account, one default campaign, and GM membership for that administrator.
- **FR-020**: The public contract used by the frontend MUST be regenerated or otherwise made available for frontend synchronization after the change.

### Key Entities

- **User Account**: A signed-in portal identity with username, password credential, global `system_role`, status, and optional default campaign.
- **System Role**: The global permission level for account administration; values are `admin` and `user`.
- **Campaign**: A JDR campaign container owned or mastered by users through memberships.
- **Campaign Membership**: The relationship between a user and a campaign, with role `gm` or `pj` and optional character association.
- **PJ**: A campaign-scoped playable character with a name, owning campaign, optional assigned user, and creation date.
- **Current Identity**: The safe frontend-facing representation of the signed-in user plus active campaign context.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of account management actions reject non-admin users while allowing admin users to complete the same actions.
- **SC-002**: 100% of campaign creation attempts by authenticated standard users succeed when valid campaign data is provided.
- **SC-003**: 0 public responses contain the retired `profile` user field or retired `player` campaign role value.
- **SC-004**: 100% of newly created PJs have a campaign association.
- **SC-005**: Existing V1 PJ frontend flows remain usable: listing PJs without a campaign filter succeeds, and creating a PJ without a campaign succeeds when a default campaign exists.
- **SC-006**: Current identity responses provide enough role information for the frontend to distinguish account administration from campaign GM/PJ capabilities in a single request.
- **SC-007**: A clean empty-environment setup can create the default administrator, default campaign, and GM membership without manual database edits.
- **SC-008**: Authorization tests cover admin-only account management, campaign creation by standard users, PJ campaign filtering, and PJ creation fallback.

## Assumptions

- The owner accepts purging impacted local/staging data instead of preserving existing identity, membership, session, and PJ rows.
- The V1 compatibility behavior for PJ creation follows Option A from the handoff: `campaign_id` may be omitted and falls back to the current user's default campaign.
- PATCH and DELETE operations for individual PJs are future-story work unless already cheap to include during implementation; BD-7 must not depend on them for completion.
- The frontend will synchronize its generated public contract after backend changes are available.
- The seeded administrator credentials are intended for local/staging reseed only and must not become production hardcoded credentials.
- Audit logs, invitation flow, fine-grained RBAC, soft-delete redesign, and cross-campaign PJ inheritance remain out of scope.
