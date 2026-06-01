# Data Model: Timezone-Aware Datetime Serialization

This feature does not introduce a new persisted business entity. It tightens the public contract for existing datetime values.

## Datetime Field

**Meaning**: Any public response value representing a date and time.

**Examples**:

- `recorded_at`
- `created_at`
- `updated_at`
- `uploaded_at`
- `completed_at`
- `generated_at`
- `queued_at`
- `started_at`
- `ended_at`
- `last_login_at`

**Validation and serialization rules**:

- Non-empty response values must include an explicit timezone suffix.
- UTC values may be serialized with either `Z` or `+00:00`.
- Naive values encountered at serialization time are interpreted as UTC.
- Aware non-UTC values are converted to UTC before output.
- `None` remains `null`; it is not replaced by the current time.
- Microsecond precision may be preserved; clients may normalize to millisecond precision.

## Existing Entities Affected

### JDR Session

**Relevant datetime fields**:

- `recorded_at`
- `created_at`
- `updated_at`

**Rules**:

- Create, detail, and list responses must expose explicit timezone suffixes.
- A timezone-qualified submitted `recorded_at` must round-trip as the same instant.
- A naive submitted `recorded_at` is treated as UTC for backward compatibility.

### JDR Character

**Relevant datetime fields**:

- `created_at`

**Rules**:

- Create and list responses must expose explicit timezone suffixes.

### JDR User

**Relevant datetime fields**:

- `created_at`
- `updated_at`
- `last_login_at`

**Rules**:

- Create, update, and list responses must expose explicit timezone suffixes for non-null fields.
- Null optional timestamps remain null.

### Auth Context

**Relevant datetime fields**:

- Any datetime added to current auth context payloads now or later.

**Rules**:

- Current auth context responses must follow the same datetime serialization rule as other public responses.
- The current BD-4 `auth/me` shape may not expose datetime fields today; tests should still protect any datetime fields present in that payload.

## State Transitions

No new state transitions are introduced. Existing creation/update flows keep their behavior, with only datetime input normalization and response serialization tightened.
