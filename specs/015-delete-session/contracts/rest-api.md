# REST API Contract: Delete JDR Session

## New Endpoint

`DELETE /services/jdr/sessions/{session_id}`

### Purpose

Permanently delete a visible JDR session and its session-scoped dependencies.

### Authentication

Same GM-only policy as existing GM session management routes:

- Authenticated GM credentials are required.
- Player credentials are rejected.
- Unknown or foreign sessions return the existing session-not-found behavior.

### Success Response: `204 No Content`

No response body.

### Error Responses

| Status | Error type | Meaning |
|--------|------------|---------|
| `401` | existing auth error | Missing or invalid authentication. |
| `403` | existing GM-only error | Authenticated caller cannot use GM session management routes. |
| `404` | `session-not-found` | Session does not exist or is not visible to the current GM. |
| `409` | `session-delete-blocked` | Session is transcribing or has an observable active RQ job and is not safe to delete yet. |
| `422` | validation error | `session_id` is not a valid UUID. |

### Deletion Effects

After a successful delete:

- `GET /services/jdr/sessions/{session_id}` returns `404 session-not-found`.
- `GET /services/jdr/sessions?campaign_id=...` no longer includes the deleted session.
- Campaign session count reflects one fewer session.
- Session-scoped audio, transcription, chunks, mapping, players, artifacts, jobs, and manual transcript override are removed or inaccessible.

### OpenAPI Requirements

- `DELETE /services/jdr/sessions/{session_id}` is present in `docs/context/api/openapi.json`.
- Success response documents `204`.
- Error responses document `404` and `409`.

## Existing Endpoint Compatibility

Existing `POST`, `GET`, and `PATCH` session endpoints keep their current semantics for sessions that have not been deleted.
