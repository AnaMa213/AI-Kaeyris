# Feature Specification: Campaigns CRUD and Session Campaign Filter

**Feature Branch**: `006-campaigns-crud-session-filter`  
**Created**: 2026-06-01  
**Status**: Draft  
**Input**: User description: "BD-6 backend handoff: Campaigns CRUD + Sessions filter by campaign_id"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - View My Campaigns (Priority: P1)

As an authenticated game master or player, I want to see the campaigns I belong to with useful summary information, so I can choose the right campaign context before working with sessions.

**Why this priority**: The frontend pivot from "Sessions" to "Campaigns" depends on a reliable campaign list before any campaign-specific workflow can be completed.

**Independent Test**: Can be tested by signing in as a user with one or more campaign memberships and verifying that only those campaigns appear with role, session count, last session date, and creation date.

**Acceptance Scenarios**:

1. **Given** an authenticated user belongs to multiple campaigns, **When** they request their campaign list, **Then** the system returns only those campaigns with the user's role in each campaign.
2. **Given** a campaign has recorded sessions, **When** it appears in the campaign list, **Then** its session count and latest session date reflect the sessions linked to that campaign.
3. **Given** a campaign has no sessions, **When** it appears in the campaign list, **Then** its session count is zero and its latest session date is empty.

---

### User Story 2 - Create and Manage a Campaign (Priority: P1)

As a game master, I want to create a campaign and update its name or description, so I can organize play around campaign-specific session histories.

**Why this priority**: The UX pivot requires users to create/select campaigns, not only consume the default campaign created by the previous feature.

**Independent Test**: Can be tested by signing in, creating a campaign with a valid name, retrieving it, updating its editable fields, and confirming that the creator is the game master for that campaign.

**Acceptance Scenarios**:

1. **Given** an authenticated user submits a valid campaign name and optional description, **When** they create a campaign, **Then** the campaign is created and the creator becomes game master for it.
2. **Given** a game master owns a campaign, **When** they update its name or description, **Then** the updated campaign details are visible when the campaign is fetched again.
3. **Given** a player is a campaign member but not a game master, **When** they try to update the campaign, **Then** the system refuses the update.

---

### User Story 3 - Use Campaign Context for Sessions (Priority: P1)

As a game master, I want sessions to be created and listed within a chosen campaign, so campaign histories stay separated and the frontend can show the right sessions for the selected campaign.

**Why this priority**: Without campaign-scoped session creation and filtering, campaigns exist as metadata but do not drive the main JDR workflow.

**Independent Test**: Can be tested by creating sessions in two campaigns for the same user and verifying that each campaign view only shows its own sessions.

**Acceptance Scenarios**:

1. **Given** a game master belongs to a campaign, **When** they create a session, **Then** they must provide the target campaign and the session is linked to that campaign.
2. **Given** a user filters sessions by a campaign they belong to, **When** sessions are returned, **Then** every returned session belongs to that campaign.
3. **Given** a user filters sessions by a campaign they do not belong to, **When** they request sessions, **Then** the system refuses access.
4. **Given** no campaign filter is supplied, **When** a user lists sessions, **Then** the existing session listing behavior remains available for backward compatibility.

---

### User Story 4 - Safely Delete Empty Campaigns (Priority: P2)

As a game master, I want to delete an unused campaign, so I can clean up mistakes without risking loss of session history.

**Why this priority**: Cleanup is useful but less critical than listing, creating, and using campaigns in the primary workflow.

**Independent Test**: Can be tested by creating an empty campaign and deleting it, then trying to delete another campaign that already has sessions.

**Acceptance Scenarios**:

1. **Given** a game master manages an empty campaign, **When** they delete it, **Then** the campaign is no longer available to its members.
2. **Given** a campaign contains one or more sessions, **When** a game master tries to delete it, **Then** the system refuses deletion and preserves the campaign and its sessions.
3. **Given** a player belongs to a campaign, **When** they try to delete it, **Then** the system refuses deletion.

### Edge Cases

- A signed-in user has no campaign memberships.
- A campaign name is empty, whitespace-only, or longer than the allowed limit.
- A campaign description is longer than the allowed limit.
- A user tries to create two campaigns with the same name under their own account.
- A user requests, updates, deletes, or filters sessions by a campaign they do not belong to.
- A player tries to create a session in a campaign where they are not a game master.
- Existing sessions created before this feature have no campaign assigned.
- Date fields returned for campaign summaries must include an explicit timezone suffix when present.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST allow an authenticated user to list all campaigns where they are a member.
- **FR-002**: Each campaign list item MUST include campaign identity, name, optional description, the current user's campaign role, session count, latest session date when available, and creation date.
- **FR-003**: The system MUST allow an authenticated user to create a campaign with a required name and optional description.
- **FR-004**: A newly created campaign MUST automatically make its creator a game master member of that campaign.
- **FR-005**: Campaign names MUST be between 1 and 200 characters after validation.
- **FR-006**: Campaign descriptions MUST be optional and limited to 4000 characters.
- **FR-007**: The system SHOULD prevent the same user from creating duplicate campaigns with the same name.
- **FR-008**: The system MUST allow a campaign member to fetch a campaign they belong to.
- **FR-009**: The system MUST reject campaign access when the authenticated user is not a member of the requested campaign.
- **FR-010**: The system MUST allow only campaign game masters to update campaign name and description.
- **FR-011**: Campaign updates MUST support changing either name, description, or both.
- **FR-012**: The system MUST allow only campaign game masters to delete campaigns.
- **FR-013**: The system MUST reject deletion of a campaign that has one or more sessions.
- **FR-014**: The system MUST preserve player characters as user-global resources for this feature; player characters MUST NOT become campaign-scoped in BD-6.
- **FR-015**: The system MUST allow authenticated users to filter session lists by a campaign they belong to.
- **FR-016**: When a session list is filtered by campaign, every returned session MUST belong to the requested campaign.
- **FR-017**: The system MUST preserve the existing unfiltered session listing behavior for backward compatibility.
- **FR-018**: Creating a new session MUST require an explicit campaign.
- **FR-019**: Creating a new session for a campaign MUST require the current user to be a game master of that campaign.
- **FR-020**: Fetching an existing session MUST reject access when the current user is not a member of the session's campaign.
- **FR-021**: Existing sessions without a campaign MUST be assigned to the creator's default campaign during the feature rollout.
- **FR-022**: Campaign summary date fields MUST use the platform's explicit timezone JSON contract.
- **FR-023**: The public machine-readable contract MUST expose the campaign management capabilities and the campaign session filtering behavior for frontend type generation.

### Key Entities

- **Campaign**: A playable campaign context with an identity, name, optional description, creation date, members, and linked sessions.
- **Campaign Membership**: The relationship between a user and a campaign, including the user's campaign role (`gm` or `player`).
- **Session**: A recorded JDR session that belongs to a campaign for BD-6 session listing and creation flows.
- **User**: An authenticated account that can belong to campaigns, create campaigns, and create campaign sessions when authorized.
- **Player Character**: A character owned by or associated with a user; remains global to the user in BD-6 and is linked to campaigns only indirectly through session participation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A signed-in user can view their campaign list and identify the correct campaign role, session count, and last session date in one request.
- **SC-002**: A game master can create a campaign and then create the first session in that campaign without manual data repair.
- **SC-003**: 100% of campaign-scoped session list results contain only sessions from the requested campaign.
- **SC-004**: 100% of cross-campaign access attempts by non-members are rejected.
- **SC-005**: Empty campaigns can be deleted by game masters, while campaigns with sessions are preserved.
- **SC-006**: Existing sessions created before BD-6 remain visible after rollout under their creator's default campaign.
- **SC-007**: The frontend can regenerate its typed client from the public contract without missing campaign CRUD or session campaign fields.

## Assumptions

- Authentication and session identity from the previous web-auth feature are reused.
- BD-4 campaign tables, memberships, user default campaign, and active campaign behavior already exist.
- The initial BD-6 behavior keeps player characters global to the user; campaign-scoped player characters are explicitly outside this feature.
- Deleting a campaign with sessions is refused in V1 to preserve history and avoid accidental data loss.
- Pagination for campaign listing uses the platform's existing default page shape and default size; advanced search and sorting are outside this feature.
- The user's default campaign remains the source of truth for backfilling legacy sessions that do not yet have a campaign.
