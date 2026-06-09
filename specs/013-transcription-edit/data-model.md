# Data Model: BD-13 Transcription Edit

## Entity: JDR Session

Existing central session entity.

### Added Field

- `edited_transcript_md`: nullable text
  - `null`: no manual override exists; Markdown reads and generation use the
    automatic transcription source.
  - non-null non-blank Markdown string: latest GM-edited transcription override.

### Existing Relevant Fields

- `id`: session identifier.
- `gm_key_id`: GM owner boundary for API-key based access.
- `campaign_id`: campaign ownership/context boundary for web-session access.
- `state`: lifecycle state; BD-13 writes require `transcribed`.
- `transcription_mode`: `diarised` or `non_diarised`.
- `campaign_context`: existing generation context; unchanged by BD-13.

### Validation Rules

- `edited_transcript_md` may be absent/null in storage.
- Write payload `content_md` must be present and must contain non-whitespace
  text.
- A save is allowed only when the session is owned by the current GM and
  `state=transcribed`.
- Saving edited Markdown replaces the previous edited Markdown for the session.
- Saving edited Markdown must not update automatic transcription rows, chunks,
  mappings, players, or generated artifacts.

### State Behavior

BD-13 does not add a new session state.

```text
created/audio_uploaded/transcribing/transcription_failed
  -- save edit --> rejected

transcribed
  -- save edit with valid content --> transcribed, edited_transcript_md set
  -- save replacement edit --> transcribed, edited_transcript_md replaced
```

## Entity: Automatic Transcription

Existing mode-specific generated source.

### Diarised Sessions

- Stored in `jdr_transcriptions.segments_json`.
- JSON transcription read remains unchanged.
- Markdown read falls back to rendered segments only when no edited Markdown
  exists.

### Non-Diarised Sessions

- Stored in `jdr_chunks.text` rows ordered by `ordre`.
- Chunk read remains unchanged.
- Markdown read should be able to return edited Markdown when present; otherwise
  it uses the existing automatic source behavior selected during implementation.

### Invariants

- Automatic chunks/segments are never rewritten by saving an edited
  transcription.
- The chosen generation source prefers edited Markdown when present.

## Entity: Transcription Edit Projection

Public response after saving an edit.

### Fields

- `session_id`: edited session identifier.
- `content_md`: latest persisted edited Markdown text.
- `is_edited`: always `true` for the save response.
- `updated_at`: session update timestamp after persistence, if available in the
  existing model.

### Notes

- This projection is intentionally smaller than `SessionOut`.
- It confirms the write without exposing automatic chunks or segments.

## Relationships

```text
GM owns Session
Session has optional Edited Transcription
Session has automatic transcription data:
  - diarised: Transcription row
  - non_diarised: Chunk rows
Generation Source Text:
  edited_transcript_md if present
  otherwise automatic source for the session mode
```

## Persistence Impact

- Alembic migration adds one nullable text column to `jdr_sessions`.
- Downgrade removes the column.
- Existing rows default to `null`, preserving current behavior.
