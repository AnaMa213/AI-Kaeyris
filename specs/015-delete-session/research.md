# Research: Delete JDR Session

## Decision 1: Block deletion when session work is active

**Decision**: Return a conflict for sessions that are currently transcribing or whose `current_job_id` resolves to an active RQ job (`queued`, `deferred`, `scheduled`, or `started`).

**Rationale**: The codebase has existing behavior that refuses audio purge while `SessionState.TRANSCRIBING` because a worker may still read/write session resources. BD-15 asks to choose either cancellation or conflict. There is no current job cancellation contract that guarantees a running RQ worker will stop before writing; returning a conflict is deterministic, testable, and does not pretend a worker was safely canceled. RQ remains the observable source for live job state; the SQL projection can be stale after completion, so terminal, missing, or expired RQ metadata does not by itself block deletion.

**Alternatives considered**:

- Cancel or abort RQ jobs during deletion: rejected for this feature because the current backend does not expose a reliable cancellation handshake with in-flight workers.
- Delete anyway and rely on cascade: rejected because a worker could continue writing after the session row is gone, creating noisy failures or partial side effects.

## Decision 2: Use hard delete for the session aggregate

**Decision**: Delete the session row after validating ownership and active-work safety. Database-owned dependencies are deleted through existing relationship cascade and foreign-key ownership.

**Rationale**: The product asks for permanent removal from campaign pages. Existing models already define session-owned dependencies (`audio_source`, `transcription`, `mappings`, `artifacts`, `jobs`, `chunks`, `session_players`) as owned by the session. SQLAlchemy documents ORM cascades for deleting related objects through relationships: https://docs.sqlalchemy.org/en/20/orm/cascades.html

**Alternatives considered**:

- Soft-delete: rejected as scope creep because the handoff does not ask for restore, retention, audit views, or hidden session filtering.
- Manual delete statement per dependency: useful as a fallback, but less aligned with the existing model ownership declarations unless tests reveal an uncovered table.

## Decision 3: Purge stored audio file best-effort before deleting SQL state

**Decision**: Reuse the existing audio path helpers and best-effort unlink behavior. Missing files should not block deletion.

**Rationale**: The current audio purge behavior treats the database as source of truth and logs filesystem unlink failures instead of turning cleanup into a failed user operation. That same posture fits session deletion: the user should not be stuck with an undeletable session because a file already disappeared.

**Alternatives considered**:

- Fail deletion if the file cannot be removed: rejected because it leaves stale product state for an operational cleanup issue.
- Leave file cleanup to a later janitor: rejected for normal success paths because BD-15 explicitly asks to purge stored audio.

## Decision 4: Keep campaign count derived from remaining sessions

**Decision**: Do not introduce a stored counter. Campaign session count continues to be derived by existing query behavior.

**Rationale**: Existing campaign summaries calculate counts from sessions. Deleting the session row naturally changes the derived count after commit.

**Alternatives considered**:

- Add or decrement a persisted counter: rejected as unnecessary state duplication for the current scale.
