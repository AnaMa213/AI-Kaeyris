"""flatten JDR elements card to free-form category-tagged rows

BD-26 / Epic 8 Story 8.3 (Option B). The elements artefact moves from four
parallel buckets ``{npcs, locations, items, clues}`` to a flat, category-tagged
list ``{"elements": [{category, name, description}]}`` so the MJ can re-file
elements under arbitrary categories. The four canonical buckets are seeded as
the labels PNJ / Lieux / Objets / Indices.

Data migration only (the text lives in a JSON column, no DDL). Idempotent: rows
already in the flat shape are left untouched. Self-contained (no app imports) so
it stays valid even as the service code evolves.

Revision ID: e4f6a8b0c121
Revises: d3e5f7a9b210
Create Date: 2026-06-29 22:15:00.000000
"""

from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e4f6a8b0c121"
down_revision: Union[str, Sequence[str], None] = "d3e5f7a9b210"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# bucket key -> display label (upgrade), and its inverse (downgrade).
_BUCKET_TO_LABEL = {
    "npcs": "PNJ",
    "locations": "Lieux",
    "items": "Objets",
    "clues": "Indices",
}
_LABEL_TO_BUCKET = {label: key for key, label in _BUCKET_TO_LABEL.items()}


def _load(content: object) -> dict:
    """content_json comes back as str (SQLite) or dict (Postgres JSON)."""
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return {}
    return content if isinstance(content, dict) else {}


def _iter_elements_rows(bind):
    rows = bind.execute(
        sa.text(
            "SELECT session_id, content_json FROM jdr_artifacts "
            "WHERE kind = 'elements'"
        )
    ).fetchall()
    return rows


def _write(bind, session_id, payload: dict) -> None:
    bind.execute(
        sa.text(
            "UPDATE jdr_artifacts SET content_json = :cj "
            "WHERE kind = 'elements' AND session_id = :sid"
        ),
        {"cj": json.dumps(payload, ensure_ascii=False), "sid": session_id},
    )


def upgrade() -> None:
    bind = op.get_bind()
    for session_id, content in _iter_elements_rows(bind):
        data = _load(content)
        if isinstance(data.get("elements"), list):
            continue  # already flat — idempotent
        flat: list[dict] = []
        for key, label in _BUCKET_TO_LABEL.items():
            for entry in data.get(key) or []:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name", "")).strip()
                if not name:
                    continue
                flat.append(
                    {
                        "category": label,
                        "name": name,
                        "description": str(entry.get("description", "")).strip(),
                    }
                )
        _write(bind, session_id, {"elements": flat})


def downgrade() -> None:
    bind = op.get_bind()
    for session_id, content in _iter_elements_rows(bind):
        data = _load(content)
        elements = data.get("elements")
        if not isinstance(elements, list):
            continue  # already in legacy shape
        buckets: dict[str, list[dict]] = {k: [] for k in _BUCKET_TO_LABEL}
        for entry in elements:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "")).strip()
            if not name:
                continue
            # Free-form categories that aren't one of the four canonical labels
            # collapse into 'clues' (best-effort; the custom category is lost).
            bucket = _LABEL_TO_BUCKET.get(str(entry.get("category", "")).strip(), "clues")
            buckets[bucket].append(
                {"name": name, "description": str(entry.get("description", "")).strip()}
            )
        _write(bind, session_id, buckets)
