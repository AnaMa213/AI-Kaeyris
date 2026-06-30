# Data Model: Epic 8 Follow-ups

No new tables or migrations are required.

## Existing Entities Touched

### Artifact

- **Existing storage**: `jdr_artifacts`
- **Relevant fields**:
  - `session_id`
  - `kind`
  - `content_json`
  - `model_used`
  - `generated_at`
  - `is_edited`
  - `edited_at`
  - `edited_by`
- **Rules added/confirmed**:
  - Manual edits must not proceed while an active artifact job can overwrite the same session's artifacts.
  - Generated artifacts default to `is_edited = false`.
  - Manual edits set `is_edited = true`, `edited_at`, and `edited_by`.
  - Text content remains untruncated when under the safety limit.

### Job

- **Existing storage**: `jdr_jobs` plus live Redis/RQ job state
- **Relevant fields**:
  - `id`
  - `kind`
  - `session_id`
  - `status`
- **Rules added/confirmed**:
  - Active artifact job kinds block manual artifact edits for the session.
  - Terminal, missing, or stale jobs do not permanently block edits.

### Elements Card

- **Existing storage**: `jdr_artifacts.content_json` with `kind = "elements"`
- **Shape**:
  - `{"elements": [{"category": str, "name": str, "description": str}, ...]}`
- **Rules added**:
  - `elements: []` is rejected unless the request explicitly confirms a full clear.
  - Confirmed empty replacement stores `{"elements": []}` and manual-edit provenance.

### Player Participation

- **Existing storage**:
  - Diarised: `jdr_session_pj_mappings`
  - Non-diarised: `jdr_session_players`
- **Rules added**:
  - Player read authorization is mode-aware.
  - Diarised sessions use speaker mapping.
  - Non-diarised sessions use player-presence rows.

## State/Validation Transitions

```text
manual artifact edit
  -> active artifact job exists
      -> reject conflict, no artifact mutation
  -> no active artifact job
      -> validate payload
      -> update artifact content + provenance

elements replacement
  -> elements non-empty
      -> update artifact
  -> elements empty + no confirmation
      -> reject validation error, no artifact mutation
  -> elements empty + confirmation
      -> update artifact to empty card + provenance

player read
  -> session mode diarised
      -> authorize by speaker mapping
  -> session mode non_diarised
      -> authorize by session player presence
```
