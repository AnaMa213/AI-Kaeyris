# Research: BD-13 Transcription Edit

## Decision: Store a single nullable Markdown override on the session

**Rationale**: The handoff asks for a session-level edited Markdown text,
separate from automatic chunks/segments, and recommends the override approach as
least invasive. The current data model already treats `Session` as the central
business entity and stores automatic transcription data in mode-specific places:
diarised output in `jdr_transcriptions`, non-diarised output in `jdr_chunks`.
Adding one nullable `edited_transcript_md` field preserves those sources while
providing a clear fallback switch.

**Alternatives considered**:

- Edit `jdr_transcriptions.segments_json`: rejected because it only covers
  diarised sessions and would require structured segment validation.
- Edit `jdr_chunks.text`: rejected because it only covers non-diarised sessions
  and would blur raw transcription data with user-edited content.
- Dedicated `jdr_transcription_edits` table: rejected for BD-13 because only one
  latest override is required; version history/reset can be a later feature.

## Decision: Use PUT for the write operation

**Rationale**: The client sends the full edited Markdown document for the
session-level override, replacing any previous override. `PUT` is defined for
creating or replacing the state of a target resource, which matches the desired
behavior. Reference: RFC 9110 section 9.3.4, `PUT`
https://www.rfc-editor.org/rfc/rfc9110#name-put

**Alternatives considered**:

- `PATCH /transcription`: acceptable for partial modification, but the payload
  is not a partial patch; it replaces the full override.
- `PUT /transcription.md`: close to the returned representation, but JSON
  payload with `content_md` is easier to expose through existing frontend API
  generation than raw Markdown request bodies.

## Decision: Return a JSON projection after saving

**Rationale**: Returning the saved content and `is_edited=true` lets the
frontend update local state immediately and gives tests a stable confirmation
surface. A `204` response would also be valid but would force the client to
perform a second read to confirm the stored value.

**Alternatives considered**:

- `204 No Content`: simpler response, but less helpful for client state and
  contract verification.
- Return the full session: rejected because editing transcription does not
  otherwise change visible session metadata.

## Decision: Gate writes on owned `state=transcribed` sessions

**Rationale**: The handoff asks for `409` when the session is not yet
transcribed because there is no completed transcription to correct. Ownership
must reuse the existing GM/campaign scoping so cross-owner attempts remain
indistinguishable from unknown sessions.

**Alternatives considered**:

- Allow edits before transcription completes: rejected because later
  transcription completion could overwrite user expectations and creates an
  unclear source of truth.
- Return `403` for cross-owner sessions: rejected because existing JDR session
  behavior hides inaccessible sessions as not found.

## Decision: Reject blank edited Markdown

**Rationale**: The spec requires empty or whitespace-only content to be rejected
so the GM cannot accidentally replace useful automatic output with an unusable
blank export. This is a validation rule on `content_md`.

**Alternatives considered**:

- Treat blank content as reset: rejected because explicit reset/delete is out of
  scope for BD-13.
- Allow blank content: rejected because it violates the feature's core value.

## Decision: Generation jobs use edited Markdown as source when present

**Rationale**: AC-B3 is product-critical: generations launched after an edit
must consume the corrected text. Narrative, elements, and POV jobs already use a
shared source-document helper, so that helper should prefer edited Markdown. The
non-diarised summary job has a separate map-reduce path over chunks, so it needs
a source selection branch that chunks the edited Markdown transiently for LLM
map/reduce while preserving original chunk rows. The existing
`text_chunker.chunk_text()` can split edited Markdown for generation without
writing those chunks back as automatic transcription data.

**Alternatives considered**:

- Copy edited text into `jdr_chunks`: rejected because it mutates automatic
  transcription data and loses the distinction required by the handoff.
- Only update `GET /transcription.md`: rejected because it would make editing
  cosmetic and fail AC-B3.
- Update only the non-diarised summary job: rejected because BD-13 asks for
  edited text to feed generated artifacts, not just one endpoint.

## Decision: Do not automatically invalidate existing artifacts on edit

**Rationale**: BD-13 requires generations launched after editing to use the
latest edited text. It does not require automatic deletion of existing summaries
or downstream artifacts at edit time. The existing `POST /artifacts/summary`
path already overwrites the summary at the end and resets downstream
non-diarised derivations during generation.

**Alternatives considered**:

- Cascade-delete summary/narrative/elements/POV immediately on edit: rejected as
  additional product behavior not requested by BD-13.
- Block editing if artifacts already exist: rejected because it prevents the GM
  from correcting text before regenerating.

## Decision: No explicit reset/delete endpoint in BD-13

**Rationale**: The handoff marks reset as optional and the feature spec keeps it
out of scope. Avoiding reset keeps the first backend slice small and focused.

**Alternatives considered**:

- `DELETE /transcription` or `content_md: null`: useful later, but would need
  its own frontend behavior, contract, and tests.
