"""Free-form elements-card helpers (BD-26 / Epic 8 Story 8.3).

Single source of truth for two things:

1. The canonical bucket -> category-label mapping. The LLM keeps producing the
   four fixed buckets (``npcs``/``locations``/``items``/``clues``); the backend
   flattens them into category-tagged rows so the MJ can then re-file elements
   under arbitrary categories.
2. Reading an elements artefact's ``content_json`` as a flat category-tagged
   list, accepting **both** the new ``{"elements": [...]}`` shape and the legacy
   four-bucket shape — so reads stay correct for any not-yet-migrated row.
"""

from __future__ import annotations

# Canonical display order; also the bucket -> label flatten mapping (FR-012).
ELEMENT_CATEGORY_LABELS: dict[str, str] = {
    "npcs": "PNJ",
    "locations": "Lieux",
    "items": "Objets",
    "clues": "Indices",
}

# Fallback category for a row whose category is blank (defensive; the edit
# endpoint rejects blank categories at the schema layer).
DEFAULT_CATEGORY = "Autres"


def _clean_row(category: object, entry: object) -> dict[str, str] | None:
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("name", "")).strip()
    if not name:
        return None
    return {
        "category": str(category).strip() or DEFAULT_CATEGORY,
        "name": name,
        "description": str(entry.get("description", "")).strip(),
    }


def flatten_elements(buckets: object) -> list[dict[str, str]]:
    """Flatten the four canonical LLM buckets into category-tagged rows."""
    rows: list[dict[str, str]] = []
    if not isinstance(buckets, dict):
        return rows
    for key, label in ELEMENT_CATEGORY_LABELS.items():
        for entry in buckets.get(key) or []:
            row = _clean_row(label, entry)
            if row is not None:
                rows.append(row)
    return rows


def elements_from_content(content: object) -> list[dict[str, str]]:
    """Read ``content_json`` as a flat category-tagged element list.

    New shape (``{"elements": [...]}``) wins; otherwise fall back to flattening
    the legacy four-bucket shape so un-migrated rows still read correctly.
    """
    if not isinstance(content, dict):
        return []
    raw = content.get("elements")
    if isinstance(raw, list):
        rows: list[dict[str, str]] = []
        for entry in raw:
            row = _clean_row(
                entry.get("category", "") if isinstance(entry, dict) else "",
                entry,
            )
            if row is not None:
                rows.append(row)
        return rows
    return flatten_elements(content)
