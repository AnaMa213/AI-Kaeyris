"""Markdown rendering for the JDR service artefacts.

ADR 0006 §5. Each artefact (transcription, narrative, elements card,
per-PJ POV) has both a JSON endpoint and a ``.md`` endpoint. The
Markdown rendering lives here, isolated from ``logic.py`` and from
the routes, so it can be unit-tested without DB or LLM.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Shared header
# ---------------------------------------------------------------------------


def render_session_header(session: Any) -> str:
    """Common Markdown preamble for every export of a session.

    The header is intentionally self-contained: pasted alone, a reader
    can tell which session the file belongs to. Format:

        # Session : <title>

        - **Date** : YYYY-MM-DD HH:MM (UTC)
        - **Identifiant** : <uuid>
        - **État** : <state>

        ---
    """
    recorded_at = getattr(session, "recorded_at", None)
    date_str = (
        recorded_at.strftime("%Y-%m-%d %H:%M (UTC)")
        if recorded_at is not None
        else "(inconnue)"
    )
    state_value = getattr(getattr(session, "state", None), "value", "")
    lines = [
        f"# Session : {session.title}",
        "",
        f"- **Date** : {date_str}",
        f"- **Identifiant** : {session.id}",
        f"- **État** : {state_value}",
        "",
        "---",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------


def render_transcription_md(
    session: Any,
    transcription: Any,
    mapping: dict[str, str] | None = None,
) -> str:
    """Render the diarised transcription as Markdown.

    One block per segment, with timestamps and the resolved speaker.
    When ``mapping`` (``{speaker_label: pj_name}``) is provided (US3),
    the PJ name precedes the raw label; otherwise the raw label stands
    alone.
    """
    parts: list[str] = [render_session_header(session)]
    parts.append("## Transcription")
    parts.append("")

    segments = list(transcription.segments_json or [])
    if not segments:
        parts.append("_(aucun segment)_")
        parts.append("")
    else:
        for seg in segments:
            label = str(seg.get("speaker_label", "unknown"))
            start = float(seg.get("start_seconds", 0.0) or 0.0)
            end = float(seg.get("end_seconds", 0.0) or 0.0)
            text = str(seg.get("text", "")).strip()
            display = _format_speaker(label, mapping)
            parts.append(
                f"**[{start:.1f}s → {end:.1f}s] {display}**"
            )
            parts.append("")
            parts.append(text if text else "_(silence)_")
            parts.append("")

    # Footer attribution
    parts.append("---")
    parts.append("")
    provider = getattr(transcription, "provider", "")
    model = getattr(transcription, "model_used", "")
    lang = getattr(transcription, "language", "")
    parts.append(
        f"_Transcription produite par `{model}` ({provider}), langue `{lang}`._"
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Narrative
# ---------------------------------------------------------------------------


def render_narrative_md(session: Any, narrative_artifact: Any) -> str:
    """Render the narrative-summary artefact as Markdown."""
    parts: list[str] = [render_session_header(session)]
    parts.append("## Résumé narratif")
    parts.append("")

    text = ""
    content = getattr(narrative_artifact, "content_json", None) or {}
    if isinstance(content, dict):
        text = str(content.get("text", "")).strip()

    if text:
        parts.append(text)
    else:
        parts.append("_(résumé vide)_")
    parts.append("")

    # Footer attribution
    parts.append("---")
    parts.append("")
    model = getattr(narrative_artifact, "model_used", "")
    generated_at = getattr(narrative_artifact, "generated_at", None)
    date_str = (
        generated_at.strftime("%Y-%m-%d %H:%M (UTC)")
        if generated_at is not None
        else "(inconnue)"
    )
    parts.append(f"_Résumé produit par `{model}`, le {date_str}._")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Placeholders for US2 / US3 (filled when the corresponding sub-lots land)
# ---------------------------------------------------------------------------


def render_elements_md(session: Any, elements_artifact: Any) -> str:
    """Render the structured-elements card with four h2 sections."""
    raise NotImplementedError("Filled in by US2.")


def render_pov_md(session: Any, pj: Any, pov_artifact: Any) -> str:
    """Render a per-PJ POV summary."""
    raise NotImplementedError("Filled in by US3.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_speaker(
    raw_label: str, mapping: dict[str, str] | None
) -> str:
    """Return the speaker display string for the Markdown export.

    With a mapping: ``Aragorn (speaker_1)``.
    Without: ``speaker_1``.
    """
    if mapping is not None and raw_label in mapping:
        return f"{mapping[raw_label]} ({raw_label})"
    return raw_label
