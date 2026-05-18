"""Unit tests for the text chunker (feature 002 — `non_diarised` mode).

Pure function tests. No DB, no async, no fixtures. The chunker is
described in `specs/002-non-diarised-mode/research.md §1`.
"""

import pytest

from app.services.jdr.text_chunker import chunk_text


def test_empty_text_returns_empty_list():
    assert chunk_text("", max_chars=100) == []
    assert chunk_text("   \n\n  ", max_chars=100) == []


def test_short_text_fits_in_single_chunk():
    text = "Bonjour à tous, voici une courte session."
    assert chunk_text(text, max_chars=100) == [text]


def test_exact_max_chars_single_chunk():
    text = "x" * 30
    assert chunk_text(text, max_chars=30) == [text]


def test_cuts_on_paragraph_break_when_present():
    para_a = "Partie A. " * 10  # 100 chars
    para_b = "Partie B. " * 10
    text = f"{para_a}\n\n{para_b}"
    chunks = chunk_text(text, max_chars=120)
    assert len(chunks) == 2
    assert chunks[0].startswith("Partie A")
    assert chunks[0].endswith(".")
    assert chunks[1].startswith("Partie B")


def test_cuts_on_sentence_end_when_no_paragraph():
    text = "Première phrase. Deuxième phrase. Troisième phrase. Quatrième phrase."
    chunks = chunk_text(text, max_chars=40)
    # Each chunk should end on a sentence-ending punctuation
    for chunk in chunks[:-1]:  # last may be whatever fits
        assert chunk[-1] in {".", "!", "?", "…"}, f"Bad ending: {chunk!r}"
    # Concatenation preserves the content (modulo whitespace)
    joined = " ".join(chunks).replace("  ", " ").strip()
    assert joined == text.strip()


def test_cuts_on_whitespace_when_no_sentence_end():
    text = "mot " * 50  # no punctuation
    chunks = chunk_text(text, max_chars=30)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 30
    # All "mot" tokens preserved
    joined = " ".join(chunks)
    assert joined.count("mot") == 50


def test_hard_cut_when_no_natural_boundary():
    # A long unbreakable token > max_chars
    text = "x" * 100
    chunks = chunk_text(text, max_chars=30)
    assert len(chunks) == 4  # 30 + 30 + 30 + 10
    assert chunks[0] == "x" * 30
    assert chunks[-1] == "x" * 10


def test_max_chars_zero_or_negative_raises():
    with pytest.raises(ValueError):
        chunk_text("hello", max_chars=0)
    with pytest.raises(ValueError):
        chunk_text("hello", max_chars=-1)


def test_preserves_order_with_long_realistic_text():
    """Realistic-ish JDR transcript fragment with mixed punctuation."""
    text = (
        "Le groupe entre dans la taverne. Aragorn commande une bière. "
        "« Que se passe-t-il ici ? » demande-t-il au barman.\n\n"
        "Le barman hésite, puis chuchote : « On dit que des hommes étranges "
        "rôdent dans la forêt voisine. » Galadriel fronce les sourcils. "
        "Elle se tourne vers ses compagnons. La décision est prise rapidement : "
        "ils iront enquêter à l'aube."
    )
    chunks = chunk_text(text, max_chars=100)
    assert len(chunks) >= 2
    # Order preserved : Aragorn before Galadriel
    full = " ".join(chunks)
    assert full.index("Aragorn") < full.index("Galadriel")


def test_no_chunk_exceeds_max_chars():
    text = "Phrase courte. " * 200  # 3000 chars total
    chunks = chunk_text(text, max_chars=80)
    for chunk in chunks:
        assert len(chunk) <= 80, f"Chunk too long: {len(chunk)} chars"
