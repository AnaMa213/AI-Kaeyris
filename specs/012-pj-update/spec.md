# Feature Specification: PJ Update

**Feature Branch**: `codex/012-pj-update`  
**Created**: 2026-06-09  
**Status**: Draft  
**Input**: Backend handoff BD-12 asks to make player characters editable after creation. Existing product flows can create and list player characters, including an optional linked user account, but cannot rename a character or change that account link later.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Rename A Player Character (Priority: P1)

As a GM managing a campaign roster, I need to rename one of my player characters after creation so that the roster stays accurate when a character name changes or was entered incorrectly.

**Why this priority**: Renaming is the smallest useful update operation and proves that existing player characters can be edited without recreating them.

**Independent Test**: Can be tested by creating two player characters for the same GM, updating the name of one, and confirming that only that character changes while the returned character details show the new name.

**Acceptance Scenarios**:

1. **Given** a GM owns a player character, **When** the GM updates the character name to a new valid value, **Then** the character details show the new name.
2. **Given** a GM owns multiple player characters, **When** the GM renames one of them, **Then** the other characters remain unchanged.
3. **Given** a GM attempts to rename a player character to a name already used by another of their characters, **When** the update is submitted, **Then** the update is rejected with the existing duplicate-character error behavior.

---

### User Story 2 - Link Or Unlink A User Account (Priority: P2)

As a GM, I need to associate or remove a user account from an existing player character so that a character can be assigned to the right player after the roster has already been created.

**Why this priority**: The frontend Epic 4 bis needs editable player-character ownership. The account link already exists in character data, but it is currently write-on-create only.

**Independent Test**: Can be tested by creating a player character, assigning it to a valid user, confirming the character details include that user, then explicitly clearing the link and confirming it is empty.

**Acceptance Scenarios**:

1. **Given** a GM owns a player character and a valid user account exists, **When** the GM links that user to the character, **Then** the character details show the linked user.
2. **Given** a player character is linked to a user account, **When** the GM clears the user link, **Then** the character details show no linked user.
3. **Given** a GM submits an unknown user account as the link target, **When** the update is submitted, **Then** the update is rejected with the existing invalid-user error behavior.

---

### User Story 3 - Protect Character Ownership Boundaries (Priority: P3)

As a GM, I must not be able to edit player characters owned by another GM so that campaign and roster data stays isolated.

**Why this priority**: Update access must preserve the same ownership boundary as existing create/list behavior.

**Independent Test**: Can be tested by creating a player character for one GM, authenticating as another GM, attempting the update, and confirming the character is hidden or unavailable to that second GM.

**Acceptance Scenarios**:

1. **Given** a player character belongs to another GM, **When** the current GM attempts to edit it, **Then** the system behaves as if the character is not found.
2. **Given** a failed cross-owner update attempt, **When** the original GM reads the character, **Then** the character data is unchanged.

### Edge Cases

- A request updates only the name.
- A request updates only the linked user account.
- A request updates both name and linked user account at the same time.
- A request explicitly clears the linked user account.
- A request contains no actual change.
- A requested new name duplicates another character belonging to the same GM.
- A requested user account does not exist.
- A GM attempts to update a character that belongs to another GM.
- Character deletion is intentionally out of scope for this feature.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: GMs MUST be able to partially update an existing player character that they own.
- **FR-002**: GMs MUST be able to change a player character's name without recreating the character.
- **FR-003**: GMs MUST be able to associate an existing user account with a player character after creation.
- **FR-004**: GMs MUST be able to explicitly remove the user-account association from a player character.
- **FR-005**: Updating a player character MUST return the updated character details using the same visible shape as existing character details.
- **FR-006**: Updating a player character MUST preserve existing ownership isolation: a GM cannot update another GM's character.
- **FR-007**: Updating a character to a duplicate name within the same GM scope MUST be rejected with the same duplicate-character behavior as character creation.
- **FR-008**: Linking an unknown user account MUST be rejected with the same invalid-user behavior as character creation.
- **FR-009**: The update capability MUST be visible in the backend contract consumed by the frontend type-generation workflow.
- **FR-010**: Character deletion MUST NOT be included in this feature.

### Key Entities

- **Player Character**: A campaign roster character owned by a GM, with a name and optional linked user account.
- **GM**: The user or credential that owns and manages player characters within their scope.
- **User Account Link**: Optional relationship between a player character and a user account that can be set, changed, or cleared.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A GM can successfully rename one of their player characters and see the new name in the returned character details in one update action.
- **SC-002**: A GM can link a valid user account to an existing player character and later clear that link in separate update actions.
- **SC-003**: 100% of tested cross-owner update attempts leave the target character unchanged and unavailable to the unauthorized GM.
- **SC-004**: 100% of tested duplicate-name and unknown-user update attempts return the expected existing error categories.
- **SC-005**: The frontend contract generation workflow can discover the player-character update operation and its editable fields.

## Assumptions

- Existing character creation and listing behavior remains unchanged.
- Existing user-account data is available for validating character links.
- Existing duplicate-name and invalid-user error categories should be reused for consistency.
- Deleting player characters is a future feature because deletion needs separate rules for mappings and session player lists.
