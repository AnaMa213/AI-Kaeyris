"""RQ jobs for the JDR service.

ADR 0006. Each job is a plain synchronous function (RQ workers run sync
callables; async work goes through ``asyncio.run``). Pickleable
arguments only — primitives or UUIDs, never live objects.

The bodies of these jobs are filled in progressively:
- ``transcribe_session_job``  -> sub-lot 3c (US1 — transcription).
- ``generate_narrative_job``  -> sub-lot 3d (US1 — narrative summary).
- ``generate_elements_job``   -> US2.
- ``generate_povs_job``       -> US3.

This module exists *now* so the routes that enqueue jobs (sub-lot 3b
upload, sub-lot 3d narrative-trigger) can import a real function. RQ
pickles the function reference, so the import path must resolve even
when the body is a stub.
"""

from uuid import UUID


def transcribe_session_job(session_id: UUID) -> None:
    """Transcribe the audio attached to a session.

    STUB at sub-lot 3b; the real body lands in sub-lot 3c (US1):
    - load session + audio from DB
    - call ``TranscriptionAdapter.transcribe``
    - persist the segments
    - delete the audio file and mark ``audio_sources.purged_at``
    - move the session to ``state='transcribed'``
    """
    raise NotImplementedError(
        f"transcribe_session_job({session_id!r}) — body lands in sub-lot 3c."
    )
