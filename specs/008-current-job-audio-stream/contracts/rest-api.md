# REST API Contract: Current Job and Audio Stream

## General Rules

- Existing authentication, campaign scoping, and Problem Details error shape remain unchanged.
- Datetime fields keep the existing explicit timezone serialization contract.
- `current_job_id` is additive and optional on session outputs.
- Cross-campaign or cross-tenant access failures must not reveal audio existence.

## Session Output

### `SessionOut`

All existing fields remain. Add:

```json
{
  "current_job_id": "99999999-9999-9999-9999-999999999999"
}
```

`current_job_id` may be `null`.

**Contract checks**:

- Present in `POST /services/jdr/sessions`, `GET /services/jdr/sessions`, and `GET /services/jdr/sessions/{session_id}` responses.
- `null` for sessions with no known transcription job.
- Non-null after audio upload creates a transcription job.
- Remains non-null after transcription success or failure.
- Returns to `null` after audio deletion.

## Upload Audio

### POST `/services/jdr/sessions/{session_id}/audio`

Existing upload contract remains.

**Additional behavior**:

- Response still includes `job_id`.
- The target session's later `SessionOut.current_job_id` equals the returned `job_id`.

**Response 202**:

```json
{
  "session_id": "11111111-1111-1111-1111-111111111111",
  "path": "audios/11111111-1111-1111-1111-111111111111.m4a",
  "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "size_bytes": 2048,
  "duration_seconds": 120,
  "uploaded_at": "2026-06-03T08:00:00+00:00",
  "job_id": "99999999-9999-9999-9999-999999999999"
}
```

## Retrieve Audio

### GET `/services/jdr/sessions/{session_id}/audio`

Returns binary audio for authorized users.

**Response 200**:

- Body: full audio bytes.
- Headers:
  - `Content-Type`: stored audio media type or `audio/mp4` default.
  - `Content-Length`: full file length.
  - `Accept-Ranges`: `bytes`.
  - `Cache-Control`: private cache policy suitable for immutable-until-replaced audio.

**Response 206**:

Returned when a valid `Range: bytes=<start>-<end>` request is provided.

- Body: requested byte range only.
- Headers:
  - `Content-Type`: stored audio media type or `audio/mp4` default.
  - `Content-Length`: returned byte count.
  - `Content-Range`: `bytes <start>-<end>/<total>`.
  - `Accept-Ranges`: `bytes`.
  - `Cache-Control`: private cache policy suitable for immutable-until-replaced audio.

**Response 404**:

- Session does not exist.
- Caller is not authorized for the session.
- No audio exists.
- Audio has been purged.
- Audio row exists but the backing file is missing.

**Response 416**:

- Provided byte range is invalid or outside the file length.

## Delete Audio

### DELETE `/services/jdr/sessions/{session_id}/audio`

Permanently removes the current audio and all data derived from it.

**Response 204**:

- Session state is reset to `created`.
- `current_job_id` is cleared.
- Source audio is absent or purged.
- Transcription output is removed.
- Non-diarised chunks and chunk summaries are removed.
- Derived artifacts tied to the previous audio are removed.
- Repeating the delete on a session already in `created` also returns 204.

**Response 404**:

- Session does not exist.
- Caller is not authorized for the session.

**Response 409**:

- Session is actively transcribing.
- The session remains unchanged.

**State Matrix**:

| State before DELETE | Expected response | Expected state after |
|---------------------|-------------------|----------------------|
| `created` | 204 | `created` |
| `audio_uploaded` | 204 | `created` |
| `transcribing` | 409 | `transcribing` |
| `transcription_failed` | 204 | `created` |
| `transcribed` | 204 | `created` |
