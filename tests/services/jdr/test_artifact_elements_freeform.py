"""Epic 8 / US2 (BD-26) — free-form category elements card.

The elements artefact is a flat category-tagged list. The MJ can replace the
whole card (PUT) with arbitrary categories and long descriptions. The
generation flatten (npcs->PNJ, ...) is covered in test_elements.py; here we
cover the edit endpoint, the read shape, and the shared helpers (which also
back the 0020 data migration's mapping).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import fakeredis
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.redis_client import get_redis
from app.services.jdr.elements import (
    elements_from_content,
    flatten_elements,
)
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    Artifact,
    Role,
    Session,
    SessionState,
)
from app.services.jdr.router import router as jdr_router


# ---------------------------------------------------------------------------
# Pure helpers (also back the 0020 migration mapping)
# ---------------------------------------------------------------------------


def test_flatten_elements_maps_buckets_to_labels():
    buckets = {
        "npcs": [{"name": "Gandalf", "description": "Magicien."}],
        "locations": [{"name": "Moria"}],
        "items": [{"name": "Anneau", "description": "Forgé."}],
        "clues": [{"name": "Mellon"}],
    }
    rows = flatten_elements(buckets)
    assert [(r["category"], r["name"]) for r in rows] == [
        ("PNJ", "Gandalf"),
        ("Lieux", "Moria"),
        ("Objets", "Anneau"),
        ("Indices", "Mellon"),
    ]
    # Count is preserved across the transform (SC-006).
    total = sum(len(v) for v in buckets.values())
    assert len(rows) == total


def test_flatten_elements_drops_nameless_entries():
    buckets = {"npcs": [{"description": "no name"}, {"name": "  "}], "locations": []}
    assert flatten_elements(buckets) == []


def test_elements_from_content_reads_flat_shape():
    content = {"elements": [{"category": "Factions", "name": "La Main Noire", "description": "x"}]}
    assert elements_from_content(content) == [
        {"category": "Factions", "name": "La Main Noire", "description": "x"}
    ]


def test_elements_from_content_falls_back_to_legacy_buckets():
    """An un-migrated bucket-shaped row still reads as a flat list."""
    content = {"npcs": [{"name": "Gimli", "description": "Nain."}], "locations": []}
    assert elements_from_content(content) == [
        {"category": "PNJ", "name": "Gimli", "description": "Nain."}
    ]


# ---------------------------------------------------------------------------
# PUT endpoint
# ---------------------------------------------------------------------------


@dataclass
class ElementsEditContext:
    plain_token: str
    session_id: UUID
    sessionmaker: async_sessionmaker


async def _seed_session_with_elements(
    sm: async_sessionmaker, *, plain_token: str = "gm-elements-edit"
) -> ElementsEditContext:
    session_id = uuid4()
    async with sm() as setup:
        gm = ApiKey(
            name=f"gm-{uuid4().hex[:8]}",
            hash=PasswordHasher().hash(plain_token),
            role=Role.GM,
            status=ApiKeyStatus.ACTIVE,
        )
        setup.add(gm)
        await setup.flush()
        setup.add(
            Session(
                id=session_id,
                title="Elements edit session",
                recorded_at=datetime.now(UTC),
                gm_key_id=gm.id,
                state=SessionState.TRANSCRIBED,
            )
        )
        setup.add(
            Artifact(
                session_id=session_id,
                kind="elements",
                content_json={
                    "elements": [
                        {"category": "PNJ", "name": "Gandalf", "description": "Magicien."}
                    ]
                },
                model_used="mock:llm",
                generated_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        await setup.commit()
    return ElementsEditContext(
        plain_token=plain_token, session_id=session_id, sessionmaker=sm
    )


@pytest_asyncio.fixture
async def ctx(db_engine: AsyncEngine) -> ElementsEditContext:
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    return await _seed_session_with_elements(sm)


def _make_jdr_app(make_db_session_dep: Callable[..., Any]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(jdr_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = lambda: fakeredis.FakeStrictRedis()
    return app


async def test_put_elements_round_trip_free_form_category(ctx, make_db_session_dep):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    # > 25 words; stripped (reads normalise surrounding whitespace).
    long_desc = ("Une faction marchande tentaculaire " + "très influente " * 10).strip()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        put = await client.put(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/elements",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
            json={
                "elements": [
                    {"category": "Factions", "name": "La Main Noire", "description": long_desc},
                    {"category": "PNJ", "name": "Gandalf", "description": "Magicien."},
                ]
            },
        )
        get = await client.get(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/elements",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert put.status_code == 200
    body = put.json()
    assert body["is_edited"] is True
    assert body["edited_at"] is not None
    cats = {e["name"]: e["category"] for e in body["elements"]}
    assert cats["La Main Noire"] == "Factions"  # free-form category kept
    # Long (> 25 words) hand-edited description accepted (FR-014).
    assert get.json()["elements"][0]["description"] == long_desc


async def test_put_elements_rejects_empty_replacement_without_confirmation(
    ctx, make_db_session_dep
):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        put = await client.put(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/elements",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
            json={"elements": []},
        )
        get = await client.get(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/elements",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert put.status_code == 422
    assert put.json()["type"].endswith("/elements-empty-clear-unconfirmed")
    assert get.status_code == 200
    assert get.json()["elements"] == [
        {"category": "PNJ", "name": "Gandalf", "description": "Magicien."}
    ]


async def test_put_elements_allows_empty_replacement_with_confirmation(
    ctx, make_db_session_dep
):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        put = await client.put(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/elements",
            params={"confirm_empty": "true"},
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
            json={"elements": []},
        )
        get = await client.get(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/elements",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert put.status_code == 200
    assert put.json()["elements"] == []
    assert put.json()["is_edited"] is True
    assert get.status_code == 200
    assert get.json()["elements"] == []


async def test_put_elements_422_on_blank_category(ctx, make_db_session_dep):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/elements",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
            json={"elements": [{"category": "  ", "name": "Sans catégorie", "description": ""}]},
        )
    assert resp.status_code == 422


async def test_put_elements_404_when_absent(ctx, make_db_session_dep):
    async with ctx.sessionmaker() as db:
        artifact = await db.get(Artifact, (ctx.session_id, "elements"))
        await db.delete(artifact)
        await db.commit()

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/elements",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
            json={"elements": [{"category": "PNJ", "name": "X", "description": ""}]},
        )
    assert resp.status_code == 404
    assert resp.json()["type"].endswith("/artifact-not-ready")
