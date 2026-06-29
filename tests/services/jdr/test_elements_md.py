"""US2 — Markdown export of the elements card.

Since BD-26 (Epic 8) the card is a flat category-tagged list: ``.md`` renders
one ``## <category>`` section per category present (the four canonical buckets
flatten to ``PNJ``/``Lieux``/``Objets``/``Indices``), in first-appearance
order, omitting empty categories.
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


def test_render_elements_md_renders_canonical_category_sections():
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
    assert "## Objets" in md  # 'items' bucket flattens to the 'Objets' label
    assert "## Indices" in md
    assert "Gandalf" in md
    assert "Moria" in md
    assert "Anneau" in md
    assert "Mellon" in md


def test_render_elements_md_keeps_canonical_section_order():
    session = _make_session()
    artifact = _make_artifact(
        {
            "npcs": [{"name": "Gandalf", "description": "Magicien."}],
            "locations": [{"name": "Moria", "description": "Mines."}],
            "items": [{"name": "Anneau", "description": "Forgé."}],
            "clues": [{"name": "Mellon", "description": "Passe."}],
        }
    )
    md = render_elements_md(session, artifact)

    assert (
        md.index("## PNJ")
        < md.index("## Lieux")
        < md.index("## Objets")
        < md.index("## Indices")
    )


def test_render_elements_md_shows_placeholder_when_card_empty():
    """An empty card shows a single placeholder (categories are now dynamic)."""
    session = _make_session()
    artifact = _make_artifact({"elements": []})
    md = render_elements_md(session, artifact)

    assert md.count("_(aucun élément)_") == 1


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


def test_render_elements_md_handles_legacy_bucket_shape():
    """A legacy bucket-shaped artifact still renders (only present categories)."""
    session = _make_session()
    artifact = _make_artifact({"npcs": [{"name": "Gimli", "description": "Nain."}]})
    md = render_elements_md(session, artifact)
    assert "## PNJ" in md
    assert "Gimli" in md
    # Empty canonical categories are omitted now (dynamic grouping).
    assert "## Lieux" not in md
    assert "## Objets" not in md
    assert "## Indices" not in md


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
