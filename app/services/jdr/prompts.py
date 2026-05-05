"""System prompts used by the JDR service.

Per CLAUDE.md §2.4 and ADR 0005 §2, the prompt is part of the *business*
domain — not of the LLM adapter. Centralising every prompt in this single
module keeps them editable without touching ``logic.py`` or the routes,
and makes them easy to diff over time.

Each constant is filled in by the corresponding user story:
- US1 (narrative summary)        -> NARRATIVE_SYSTEM_PROMPT
- US2 (structured elements card) -> ELEMENTS_SYSTEM_PROMPT
- US3 (per-PJ POV)               -> POV_SYSTEM_PROMPT

The prompts are passed verbatim to ``app.jobs.llm.llm_complete`` as the
``system`` argument; the user-provided context (transcription, PJ name,
…) goes into ``user``.
"""

NARRATIVE_SYSTEM_PROMPT: str = ""
ELEMENTS_SYSTEM_PROMPT: str = ""
POV_SYSTEM_PROMPT: str = ""
