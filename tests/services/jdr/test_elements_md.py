"""US2 — Markdown export of the elements card.

Per T039: ``GET /artifacts/elements.md`` produces four h2 sections named
``## PNJ``, ``## Lieux``, ``## Items``, ``## Indices``. Empty categories
still appear so that the document shape is stable.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.services.jdr.db.models import SessionState
from app.services.jdr.markdown import render_elements_md


def _make_session(**overrides):
    defaults = {
        "id": uuid4(),
        "title": "La Forge du Roi Sous la Montagne",
        "recorded_at": datetime(2026, 5, 1, 19, 30, tzinfo=UTC),
        "state": SimpleNamespace(value=SessionState.TRANSCRIBED.value),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_artifact(content: dict, *, model_used: str = "mock:llama3.1"):
    return SimpleNamespace(
        content_json=content,
        model_used=model_used,
        generated_at=datetime(2026, 5, 2, 9, 0, tzinfo=UTC),
    )


def test_render_elements_md_has_four_h2_sections_in_french():
    session = _make_session()
    artifact = _make_artifact(
        {
            "npcs": [{"name": "Gandalf", "description": "Magicien gris."}],
            "locations": [{"name": "Moria", "description": "Mines naines."}],
            "items": [{"name": "Anneau", "description": "Forgé en Mordor."}],
            "clues": [{"name": "Mellon", "description": "Mot de passe elfe."}],
        }
    )
    md = render_elements_md(session, artifact)

    assert "## PNJ" in md
    assert "## Lieux" in md
    assert "## Items" in md
    assert "## Indices" in md
    assert "Gandalf" in md
    assert "Moria" in md
    assert "Anneau" in md
    assert "Mellon" in md


def test_render_elements_md_keeps_section_order_pnj_lieux_items_indices():
    session = _make_session()
    artifact = _make_artifact(
        {"npcs": [], "locations": [], "items": [], "clues": []}
    )
    md = render_elements_md(session, artifact)

    idx_pnj = md.index("## PNJ")
    idx_lieux = md.index("## Lieux")
    idx_items = md.index("## Items")
    idx_indices = md.index("## Indices")
    assert idx_pnj < idx_lieux < idx_items < idx_indices


def test_render_elements_md_shows_empty_placeholder_for_empty_lists():
    """US 2.3: empty categories stay visible — not omitted."""
    session = _make_session()
    artifact = _make_artifact(
        {"npcs": [], "locations": [], "items": [], "clues": []}
    )
    md = render_elements_md(session, artifact)

    assert md.count("_(aucun élément)_") == 4


def test_render_elements_md_renders_entry_as_bold_name_dash_description():
    session = _make_session()
    artifact = _make_artifact(
        {
            "npcs": [{"name": "Aragorn", "description": "Roi en exil."}],
            "locations": [],
            "items": [],
            "clues": [],
        }
    )
    md = render_elements_md(session, artifact)
    assert "- **Aragorn** — Roi en exil." in md


def test_render_elements_md_omits_description_when_missing():
    session = _make_session()
    artifact = _make_artifact(
        {
            "npcs": [{"name": "Tom Bombadil", "description": ""}],
            "locations": [],
            "items": [],
            "clues": [],
        }
    )
    md = render_elements_md(session, artifact)
    assert "- **Tom Bombadil**" in md
    assert "Tom Bombadil — " not in md


def test_render_elements_md_filters_entries_without_name():
    session = _make_session()
    artifact = _make_artifact(
        {
            "npcs": [
                {"name": "Frodon", "description": "Porteur de l'Anneau."},
                {"description": "Sans nom — ignoré."},
                {"name": "", "description": "Vide — ignoré."},
            ],
            "locations": [],
            "items": [],
            "clues": [],
        }
    )
    md = render_elements_md(session, artifact)
    assert "Frodon" in md
    assert "Sans nom" not in md
    assert "Vide" not in md


def test_render_elements_md_handles_missing_content_keys():
    """A legacy artifact without one of the four keys still renders."""
    session = _make_session()
    artifact = _make_artifact({"npcs": [{"name": "Gimli", "description": "Nain."}]})
    md = render_elements_md(session, artifact)
    assert "## PNJ" in md
    assert "## Lieux" in md
    assert "## Items" in md
    assert "## Indices" in md
    assert "Gimli" in md


def test_render_elements_md_includes_session_header_and_footer():
    session = _make_session(title="Aventure dans la Comté")
    artifact = _make_artifact(
        {"npcs": [], "locations": [], "items": [], "clues": []},
        model_used="mock:llama3.1",
    )
    md = render_elements_md(session, artifact)
    assert "Aventure dans la Comté" in md
    assert "mock:llama3.1" in md
    assert md.strip().endswith("._")
