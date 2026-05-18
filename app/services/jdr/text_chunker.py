"""Text chunker for the `non_diarised` transcription pipeline.

Splits a long text into chunks of at most `max_chars` characters,
preferring natural boundaries (paragraph break, sentence end, whitespace,
then hard cut). Pure function — no I/O, no DB, no dependencies beyond
the Python stdlib. See `specs/002-non-diarised-mode/research.md §1` for
the algorithm rationale.
"""

from __future__ import annotations

import re

# Sentence-ending punctuation followed by whitespace.
_SENTENCE_END_RE = re.compile(r"[.!?…]\s")


def chunk_text(text: str, *, max_chars: int) -> list[str]:
    """Split ``text`` into chunks of at most ``max_chars`` characters.

    Boundary priority (from best to worst):
        1. paragraph break (``\\n\\n``)
        2. sentence end (``[.!?…]`` followed by whitespace)
        3. whitespace
        4. hard cut at exactly ``max_chars``

    Each returned chunk is stripped of surrounding whitespace. Empty
    chunks are skipped.
    """
    if max_chars <= 0:
        raise ValueError(f"max_chars must be > 0 (got {max_chars!r})")

    stripped = text.strip()
    if not stripped:
        return []

    chunks: list[str] = []
    remaining = stripped
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining.strip())
            break

        cut_at = _find_cut_point(remaining, max_chars)
        head = remaining[:cut_at].strip()
        if head:
            chunks.append(head)
        remaining = remaining[cut_at:].lstrip()

    return chunks


def _find_cut_point(text: str, max_chars: int) -> int:
    """Find the best index ``i ∈ [1, max_chars]`` where to split ``text``.

    Caller has already ensured ``len(text) > max_chars``. Search in the
    window ``[0, max_chars]`` for the rightmost natural boundary.
    """
    window = text[:max_chars]

    # 1) paragraph break
    para = window.rfind("\n\n")
    if para >= 1:
        return para + 2  # include the two newlines on the head side

    # 2) sentence end
    sentence_match: re.Match[str] | None = None
    for match in _SENTENCE_END_RE.finditer(window):
        sentence_match = match
    if sentence_match is not None:
        return sentence_match.end()

    # 3) whitespace
    space = window.rfind(" ")
    if space >= 1:
        return space + 1

    # 4) hard cut
    return max_chars
