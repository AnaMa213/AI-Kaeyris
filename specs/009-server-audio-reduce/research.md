# Research: Server Audio Reduce

## Decision 1: Keep One User-Visible Transcription Job

**Decision**: Keep `JobKind.TRANSCRIPTION` as the only frontend-visible job for the upload -> prepare audio -> transcribe pipeline.

**Rationale**: The feature value is to remove browser-side reduction without changing the frontend workflow. The existing BD-8 behavior already exposes `Session.current_job_id` and the frontend polls one job. A second visible `audio_reduce` job would add state transitions, labels, and handoff semantics without a current operational need. This follows the project YAGNI rule and keeps the JDR service contract stable.

**Alternatives considered**:

- Add a new `audio_reduce` job kind and chain it before transcription. Rejected for now because it increases API and UI surface. RQ supports job dependencies (`depends_on` / `Dependency`), but the product does not need a separately visible phase yet. Source: RQ docs, https://python-rq.org/docs/
- Add a new session state such as `reducing`. Rejected because the frontend explicitly prefers the existing lifecycle and no user-facing state is required to complete the task.

## Decision 2: Introduce a Durable Prepared Audio Artifact

**Decision**: Treat server-side reduction as preparation of a durable "prepared audio" file associated with the existing session audio lifecycle.

**Rationale**: Current `chunked_audio()` creates temporary WAV chunks and deletes them after transcription. BD-9 needs a reduced artifact that survives long enough for transcription, retry, playback, and destructive delete semantics. Storing prepared audio as the canonical retained file lets the raw upload be removed after successful preparation while keeping the existing session audio behaviors from BD-8.

**Alternatives considered**:

- Keep only temporary chunks. Rejected because retry and playback would have no stable prepared artifact after raw deletion.
- Keep raw and prepared files forever. Rejected because raw long-session recordings are the largest storage cost, and the spec explicitly chooses raw deletion after preparation unless a future product decision changes it.
- Add a generic media service. Rejected because audio remains part of the JDR session lifecycle for this milestone.

## Decision 3: Make Upload Size a Configured Product Limit

**Decision**: Add a documented maximum raw upload size with a default product target of 500 MB, enforced while streaming the incoming file to disk.

**Rationale**: The current code streams chunks from `UploadFile.read(_CHUNK_SIZE)` to disk and computes size afterward. That is memory-friendly, but it does not give the product a clear too-large response. OWASP API4:2023 calls out missing or inappropriate file upload limits as an unrestricted resource consumption risk, so BD-9 should make the limit explicit and testable. Source: OWASP API Security Top 10 API4:2023, https://owasp.org/API-Security/editions/2023/en/0xa4-unrestricted-resource-consumption/

**Alternatives considered**:

- Rely only on reverse proxy limits. Rejected because local development and Docker Compose still need a backend-visible behavior and documentation.
- Set no backend limit. Rejected for security and Raspberry Pi storage protection.
- Hardcode 500 MB. Rejected because deployment constraints can be lower; config keeps 12-Factor compatibility for environment-specific values. Source: 12-Factor config, https://12factor.net/config

## Decision 4: Prepare Audio in the Worker, Not During HTTP Upload

**Decision**: The HTTP upload path should only validate, persist the raw file, record metadata, and enqueue the existing transcription job. Audio preparation runs inside the worker before transcription.

**Rationale**: Audio preparation can be CPU-heavy and long-running. Keeping it in the worker preserves the current asynchronous product behavior: upload returns accepted, then polling tracks processing. FastAPI's `UploadFile` is suitable for file uploads because it exposes a spooled file and async file-like interface, but the request should not wait for a full audio transcode. Source: FastAPI request files docs, https://fastapi.tiangolo.com/tutorial/request-files/

**Alternatives considered**:

- Prepare synchronously during upload. Rejected because it turns a long CPU task into a request-time operation.
- Prepare in the browser. Rejected by the handoff: the current browser reducer is disabled and structurally fragile for long recordings and mobile devices.

## Decision 5: Keep Existing Chunked Transcription as a Downstream Step

**Decision**: Server-side audio preparation should feed the existing transcription path, including chunked transcription for long sessions.

**Rationale**: The code already has `_transcribe_with_optional_chunking()` and `chunked_audio()`, plus Docker runtime support for `ffmpeg` and `ffprobe`. BD-9 should evolve that existing worker path rather than replacing it wholesale. This preserves the non-diarised/diarised fork and the tests around timestamp shifting.

**Alternatives considered**:

- Replace chunked transcription with one single prepared file transcription call. Rejected because the existing implementation intentionally chunks before adapter calls.
- Introduce a new adapter for audio reduction. Deferred. A narrow helper in `app/services/jdr/audio.py` is enough unless another service needs the same behavior.

## Decision 6: Failure Semantics

**Decision**: Preparation failure marks the existing transcription job/session flow as failed and preserves enough diagnostic context for the frontend to show failure. Transcription failure after successful preparation keeps the prepared artifact for retry/delete behavior.

**Rationale**: From the user perspective, preparation is part of "transcription processing". A failure before transcription is still a failed processing outcome, not a separate feature state. Keeping the prepared artifact after downstream failure aligns with BD-8, where failed transcription preserves audio until explicit delete.

**Alternatives considered**:

- Roll the session back to `audio_uploaded` on any preparation failure. Rejected because permanent bad audio should not loop forever.
- Delete every artifact on any failure. Rejected because a retry or inspection path needs the retained prepared audio where available.

## Open Items Resolved

- **Job kind**: resolved to one visible `transcription` job.
- **Raw retention**: resolved to delete raw after successful preparation.
- **Upload limit**: resolved to default 500 MB product target, with deployment override and documentation if lower.
