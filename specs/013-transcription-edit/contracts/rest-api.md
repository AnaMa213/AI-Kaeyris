# REST API Contract: BD-13 Transcription Edit

## PUT `/services/jdr/sessions/{session_id}/transcription`

Save or replace the GM-edited Markdown transcription override for a transcribed
session.

### Authentication

- Requires existing GM authentication.
- Session must belong to the current GM/campaign scope.

### Request Body

```json
{
  "content_md": "## Scène 1\n\n**Aldric** : texte corrigé..."
}
```

### Request Schema

| Field | Type | Required | Rules |
|-------|------|----------|-------|
| `content_md` | string | yes | Must contain non-whitespace Markdown text |

### Success Response: `200 OK`

```json
{
  "session_id": "00000000-0000-0000-0000-000000000000",
  "content_md": "## Scène 1\n\n**Aldric** : texte corrigé...",
  "is_edited": true,
  "updated_at": "2026-06-09T12:00:00Z"
}
```

### Response Schema

| Field | Type | Notes |
|-------|------|-------|
| `session_id` | UUID string | Edited session |
| `content_md` | string | Persisted edited Markdown |
| `is_edited` | boolean | `true` for this response |
| `updated_at` | ISO datetime | Session update timestamp, if model exposes it |

### Error Responses

| Status | Type Suffix | Condition |
|--------|-------------|-----------|
| `401` | existing auth error | Missing/invalid authentication |
| `404` | `session-not-found` | Session does not exist or is not visible to current GM |
| `409` | `session-not-transcribed` | Session is not in `transcribed` state |
| `422` | validation error | `content_md` is missing, null, empty, or whitespace-only |

## GET `/services/jdr/sessions/{session_id}/transcription.md`

Existing Markdown transcription export.

### BD-13 Behavior Change

- If the session has edited Markdown, return that exact edited Markdown.
- If the session has no edited Markdown, preserve existing automatic Markdown
  behavior.

### Success Response: `200 OK`

Headers:

```http
Content-Type: text/markdown; charset=utf-8
```

Body:

```markdown
## Scène 1

**Aldric** : texte corrigé...
```

### Error Responses

Existing ownership and not-ready behavior remains unchanged except that a
completed session with edited Markdown can return the override.

## Artifact Generation Endpoints

Existing generation enqueue endpoints, including summary, narrative, elements,
and POV jobs.

### BD-13 Behavior Change

- If `edited_transcript_md` exists, generation jobs launched after the edit use
  that edited Markdown as source text.
- If no edited Markdown exists, generation jobs use the existing automatic
  source for their session mode.

### Contract Note

The public request/response shape of existing generation endpoints does not
change.

## OpenAPI Expectations

- OpenAPI must expose `PUT /services/jdr/sessions/{session_id}/transcription`.
- OpenAPI must expose a request schema with required `content_md`.
- OpenAPI must expose the save response schema.
- Existing `GET /transcription.md` remains documented.
