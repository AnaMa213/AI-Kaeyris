# REST API Contract: Timezone-Aware Datetime Serialization

This feature updates the shared response contract for existing public endpoints. It does not add endpoints.

## Global Response Rule

Every non-null datetime field in public JSON responses MUST be an ISO-8601-compatible string with an explicit timezone suffix.

Accepted UTC examples:

```json
"2026-05-31T18:00:00Z"
```

```json
"2026-05-31T18:00:00+00:00"
```

Rejected example:

```json
"2026-05-31T18:00:00"
```

## Input Compatibility Rule

Existing datetime inputs remain accepted:

```json
{ "recorded_at": "2026-05-31T18:00:00Z" }
```

```json
{ "recorded_at": "2026-05-31T20:00:00+02:00" }
```

```json
{ "recorded_at": "2026-05-31T18:00:00" }
```

The timezone-naive input form is interpreted as UTC for backward compatibility.

## Covered Endpoints

### `POST /services/jdr/sessions`

Response datetime fields:

- `recorded_at`
- `created_at`
- `updated_at`

Contract:

- Each listed field includes `Z` or a numeric timezone offset.
- `recorded_at` represents the same instant submitted by the client.

### `GET /services/jdr/sessions/{id}`

Response datetime fields:

- `recorded_at`
- `created_at`
- `updated_at`

Contract:

- Each listed field includes `Z` or a numeric timezone offset.
- Values remain consistent with create response values after normalization.

### `GET /services/jdr/sessions`

Response datetime fields:

- Every datetime field on each `items[]` entry.

Contract:

- List responses follow the same rule as detail responses.

### `POST /services/jdr/pjs`

Response datetime fields:

- `created_at`

Contract:

- `created_at` includes `Z` or a numeric timezone offset.

### `GET /services/jdr/pjs`

Response datetime fields:

- Every datetime field on each `items[]` entry.

Contract:

- List responses follow the same rule as create responses.

### `POST /services/jdr/users`

Response datetime fields:

- `created_at`
- `updated_at`
- `last_login_at` when non-null

Contract:

- Non-null datetime fields include `Z` or a numeric timezone offset.

### `GET /services/jdr/users`

Response datetime fields:

- Every datetime field on each `items[]` entry.

Contract:

- List responses follow the same rule as create/update responses.

### `GET /services/jdr/auth/me`

Response datetime fields:

- Any datetime field present in the payload.

Contract:

- If the current auth context payload contains datetime fields, each non-null value includes `Z` or a numeric timezone offset.
- If the payload contains no datetime fields, the endpoint requires no shape change.

## Contract Test Heuristic

A response datetime string passes if:

- It matches an explicit timezone suffix: `Z`, `+HH:MM`, or `-HH:MM`.
- It can be parsed by a standard web client as an absolute instant.
- For UTC round trips, normalized client output represents the same instant as the submitted value.
