# Feature Specification: Timezone-Aware Datetime Serialization

**Feature Branch**: `[005-tz-aware-datetime-serialization]`  
**Created**: 2026-06-01  
**Status**: Draft  
**Input**: User description: "BD-5 backend handoff: all backend datetime fields currently serialize without an explicit timezone suffix, causing JavaScript clients to display shifted times. Backend responses must expose timezone-aware ISO-8601 values with `Z` or an explicit offset while keeping existing datetime input compatibility."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - View JDR Session Times Without Silent Shift (Priority: P1)

As a JDR web user, I want session dates returned by the backend to carry an explicit timezone so that the frontend displays the intended local time instead of silently shifting it.

**Why this priority**: This is the observed user-facing bug. A session recorded at a known instant can appear two hours earlier for a user in France when the response omits the timezone suffix.

**Independent Test**: Can be fully tested by creating a JDR session with a timezone-qualified datetime, reading the creation response, and confirming the returned datetime includes an explicit timezone and represents the same instant.

**Acceptance Scenarios**:

1. **Given** an authenticated user submits a session datetime with a `Z` suffix, **When** the session is created, **Then** the response returns `recorded_at`, `created_at`, and `updated_at` with either `Z` or an explicit `+HH:MM` offset.
2. **Given** an authenticated user reads the created session later, **When** the session detail is returned, **Then** the same datetime fields still include an explicit timezone and represent the same instant as creation.

---

### User Story 2 - List Resources With Consistent Datetime Contract (Priority: P2)

As a frontend maintainer, I want every backend list and detail response to use the same timezone-aware datetime contract so that date parsing can be simple and consistent across JDR screens.

**Why this priority**: The bug is confirmed on multiple JDR endpoints and presumed wherever backend datetime fields are serialized. Fixing only one endpoint would leave inconsistent behavior and hidden frontend workarounds.

**Independent Test**: Can be tested by requesting representative session, character, user, and current-auth-context responses and checking every datetime string exposed in those payloads.

**Acceptance Scenarios**:

1. **Given** existing JDR sessions, characters, users, or auth context data, **When** a client requests list or detail endpoints, **Then** every datetime field in the response includes an explicit timezone suffix.
2. **Given** a response contains nested objects with datetime fields, **When** the payload is inspected, **Then** nested datetime fields follow the same explicit-timezone rule as top-level fields.

---

### User Story 3 - Keep Existing Datetime Inputs Compatible (Priority: P3)

As an API client, I want existing datetime input formats to keep working so that this backend correction does not break current integrations.

**Why this priority**: The frontend already sends valid timezone-qualified datetimes, and the handoff explicitly requires no input contract break. Compatibility protects existing clients while the output contract is corrected.

**Independent Test**: Can be tested by submitting accepted datetime input variants and confirming successful creation or update with timezone-aware response values.

**Acceptance Scenarios**:

1. **Given** a client submits a datetime with `Z`, **When** the backend accepts the request, **Then** the response uses an explicit timezone and preserves the same instant.
2. **Given** a client submits a datetime with an explicit numeric offset, **When** the backend accepts the request, **Then** the response uses an explicit timezone and preserves the same instant.
3. **Given** a legacy client submits a datetime without a timezone, **When** the backend accepts the request, **Then** the value is interpreted consistently as UTC and the response includes an explicit UTC timezone.

### Edge Cases

- Datetime values with microsecond precision must remain valid and must not lose more precision than existing client display paths can safely ignore.
- Datetime values created by the server itself must include an explicit timezone in responses, not only datetimes submitted by clients.
- Empty optional datetime fields remain empty and must not be replaced with a current timestamp.
- All datetime fields exposed by existing public JDR responses are in scope, including `recorded_at`, `created_at`, `updated_at`, and any datetime fields in current-user or nested context payloads.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST serialize every non-empty datetime field in public backend responses with an explicit timezone suffix.
- **FR-002**: The system MUST use an ISO-8601-compatible datetime representation that standard web clients can parse as an absolute instant without assuming the user's local machine timezone.
- **FR-003**: The system MUST preserve the represented instant when a client submits a timezone-qualified datetime and later reads it back.
- **FR-004**: The system MUST treat accepted timezone-naive datetime inputs as UTC for backward compatibility.
- **FR-005**: The system MUST keep accepting existing supported datetime input variants: `Z` suffix, numeric offset, and timezone-naive values.
- **FR-006**: The system MUST apply the explicit-timezone output contract globally to existing public response payloads, rather than relying on endpoint-specific exceptions.
- **FR-007**: The system MUST include tests covering create, detail, list, and current-auth-context response payloads that expose datetime fields.
- **FR-008**: The system MUST keep existing non-datetime response fields and request bodies unchanged.

### Key Entities *(include if feature involves data)*

- **Datetime Field**: Any response value representing a date and time, such as creation, update, recording, or context timestamps.
- **JDR Session**: A role-playing session record with user-provided and server-managed datetime fields.
- **JDR Character**: A player character record with server-managed datetime metadata.
- **JDR User/Auth Context**: User and current-auth-context response data that may include server-managed datetime metadata.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of non-empty datetime fields in covered public response payloads include either `Z` or a numeric timezone offset.
- **SC-002**: A session created with a UTC datetime is displayed by a standard web client as the same instant after round trip, with no timezone-offset drift.
- **SC-003**: Existing accepted datetime input formats continue to succeed for create or update flows covered by this feature.
- **SC-004**: Regression tests fail if a covered response emits a timezone-naive datetime string.
- **SC-005**: No existing non-datetime request or response contract changes are introduced by this feature.

## Assumptions

- UTC is the canonical interpretation for legacy timezone-naive datetime inputs.
- The output may use either `Z` or `+00:00` for UTC, and either form is acceptable as long as it is explicit and consistent enough for clients to parse safely.
- Frontend-local formatting remains outside this backend feature; clients may still display the same instant in the user's local timezone.
- The already-existing frontend workaround remains harmless during rollout and can be removed separately after the backend contract is corrected.
