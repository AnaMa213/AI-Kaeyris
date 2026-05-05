"""Markdown rendering for the JDR service artefacts.

ADR 0006 §5. Each artefact (transcription, narrative, elements card,
per-PJ POV) has both a JSON endpoint (consumed by clients that want
structured data) and a ``.md`` endpoint (consumed by the MJ as a
ready-to-paste recap). The Markdown rendering lives here, isolated
from ``logic.py``, so it can be unit-tested without DB or LLM.

Function bodies are stubbed (``NotImplementedError``) at this jalon —
each user story fills in the renderer it needs:
- US1 -> render_transcription_md, render_narrative_md
- US2 -> render_elements_md
- US3 -> render_pov_md
"""

from typing import Any


def render_session_header(session: Any) -> str:
    """Common Markdown preamble for every export of a session.

    Used by every ``render_*_md`` function so the produced files share
    the same ``# Session …`` block (title, recorded date, MJ).
    """
    raise NotImplementedError("Filled in by US1.")


def render_transcription_md(
    session: Any,
    transcription: Any,
    mapping: Any | None = None,
) -> str:
    """Render the diarised transcription as Markdown.

    One paragraph per turn. The speaker label uses the resolved PJ name
    when ``mapping`` is provided, otherwise the raw ``speaker_X`` label.
    """
    raise NotImplementedError("Filled in by US1.")


def render_narrative_md(session: Any, narrative_artifact: Any) -> str:
    """Render the narrative-summary artefact as Markdown."""
    raise NotImplementedError("Filled in by US1.")


def render_elements_md(session: Any, elements_artifact: Any) -> str:
    """Render the structured-elements card with four h2 sections."""
    raise NotImplementedError("Filled in by US2.")


def render_pov_md(session: Any, pj: Any, pov_artifact: Any) -> str:
    """Render a per-PJ POV summary."""
    raise NotImplementedError("Filled in by US3.")
