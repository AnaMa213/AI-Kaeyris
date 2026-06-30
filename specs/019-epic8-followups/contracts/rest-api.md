# REST API Contract: Epic 8 Follow-ups

All endpoints keep their existing authentication requirements and response models unless explicitly noted.

## Manual Artifact Edits

### Affected endpoints

- `PATCH /services/jdr/sessions/{session_id}/artifacts/summary`
- `PATCH /services/jdr/sessions/{session_id}/artifacts/narrative`
- `PATCH /services/jdr/sessions/{session_id}/artifacts/povs/{pj_id}`
- `PUT /services/jdr/sessions/{session_id}/artifacts/elements`

### New conflict response

When the session has an active artifact generation job:

```http
HTTP/1.1 409 Conflict
Content-Type: application/problem+json
```

```json
{
  "type": "https://errors.ai-kaeyris.local/jdr/artifact-busy",
  "title": "Artifact generation in progress",
  "status": 409,
  "detail": "An artifact generation job is still running for this session. Retry after it completes."
}
```

The artifact content and provenance must remain unchanged.

## Elements Empty Replacement Confirmation

### Request without confirmation

```http
PUT /services/jdr/sessions/{session_id}/artifacts/elements
Content-Type: application/json
```

```json
{ "elements": [] }
```

Expected response:

```http
HTTP/1.1 422 Unprocessable Entity
Content-Type: application/problem+json
```

### Request with confirmation

```http
PUT /services/jdr/sessions/{session_id}/artifacts/elements?confirm_empty=true
Content-Type: application/json
```

```json
{ "elements": [] }
```

Expected response: `200 OK` with the existing `ElementsArtifactOut` shape and `elements: []`.

## Text Edit Safety Limit

### Affected endpoints

- `PATCH /services/jdr/sessions/{session_id}/artifacts/summary`
- `PATCH /services/jdr/sessions/{session_id}/artifacts/narrative`
- `PATCH /services/jdr/sessions/{session_id}/artifacts/povs/{pj_id}`

Payloads above the documented safety limit return `422 Unprocessable Entity` and do not mutate the artifact.

## Player Reads In Non-Diarised Sessions

### Affected endpoints

- `GET /services/jdr/me/sessions`
- `GET /services/jdr/me/sessions/{session_id}/narrative`
- `GET /services/jdr/me/sessions/{session_id}/narrative.md`
- `GET /services/jdr/me/sessions/{session_id}/summary`
- `GET /services/jdr/me/sessions/{session_id}/summary.md`
- `GET /services/jdr/me/sessions/{session_id}/elements`
- `GET /services/jdr/me/sessions/{session_id}/elements.md`
- `GET /services/jdr/me/sessions/{session_id}/pov`
- `GET /services/jdr/me/sessions/{session_id}/pov.md`

### Authorization semantics

- Diarised sessions: authorized when the player's PJ appears in `speaker -> PJ` mapping.
- Non-diarised sessions: authorized when the player's PJ appears in the session player-presence list.
- Existing response codes for unmapped/non-participating sessions remain `403`.
