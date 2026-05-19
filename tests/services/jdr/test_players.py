"""US3 / feature 002 — Endpoint `/sessions/{id}/players`.

Déclaration des PJ présents en mode `non_diarised` (FR-012).
Symétrique de `/mapping` mais sans speaker_label. Sémantique PUT-like
(remplacement intégral). Validation MJ ownership. Réservé non_diarised.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import fakeredis
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.redis_client import get_redis
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    Pj,
    Role,
    Session,
    SessionPlayer,
    SessionState,
    TranscriptionMode,
)
from app.services.jdr.router import router as jdr_router


def _make_jdr_app(make_db_session_dep: Callable[..., Any]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(jdr_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = lambda: fakeredis.FakeStrictRedis()
    return app


async def _seed_gm(db_session, plain_token: str) -> ApiKey:
    gm = ApiKey(
        name=f"gm-{uuid4().hex[:8]}",
        hash=PasswordHasher().hash(plain_token),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
    )
    db_session.add(gm)
    await db_session.commit()
    await db_session.refresh(gm)
    return gm


async def _seed_pj(db_session, *, gm_id, name: str) -> Pj:
    pj = Pj(name=name, owner_gm_key_id=gm_id)
    db_session.add(pj)
    await db_session.commit()
    await db_session.refresh(pj)
    return pj


async def _seed_session(
    db_session, *, gm_id, mode: TranscriptionMode = TranscriptionMode.NON_DIARISED
) -> Session:
    session = Session(
        title="Players test",
        recorded_at=datetime.now(UTC),
        gm_key_id=gm_id,
        state=SessionState.TRANSCRIBED,
        transcription_mode=mode,
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)
    return session


# ---------------------------------------------------------------------------
# POST /players — happy path
# ---------------------------------------------------------------------------


async def test_post_players_replaces_list(db_session, make_db_session_dep):
    plain = "gm-players-ok"
    gm = await _seed_gm(db_session, plain)
    pj_a = await _seed_pj(db_session, gm_id=gm.id, name="Aragorn")
    pj_b = await _seed_pj(db_session, gm_id=gm.id, name="Galadriel")
    pj_c = await _seed_pj(db_session, gm_id=gm.id, name="Frodon")
    session = await _seed_session(db_session, gm_id=gm.id)

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Première déclaration : 2 PJ
        first = await client.post(
            f"/services/jdr/sessions/{session.id}/players",
            json={"pj_ids": [str(pj_a.id), str(pj_b.id)]},
            headers={"Authorization": f"Bearer {plain}"},
        )
        assert first.status_code == 200
        assert set(first.json()["pj_ids"]) == {str(pj_a.id), str(pj_b.id)}
        # Replacement par 1 seul PJ : la liste précédente disparaît
        second = await client.post(
            f"/services/jdr/sessions/{session.id}/players",
            json={"pj_ids": [str(pj_c.id)]},
            headers={"Authorization": f"Bearer {plain}"},
        )
        assert second.status_code == 200
        assert second.json()["pj_ids"] == [str(pj_c.id)]

    # DB state
    async with db_session.bind.dispose() if False else _noop():
        pass
    rows = (
        await db_session.execute(
            select(SessionPlayer).where(SessionPlayer.session_id == session.id)
        )
    ).scalars().all()
    assert {r.pj_id for r in rows} == {pj_c.id}


class _noop:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


async def test_get_players_returns_current_list(db_session, make_db_session_dep):
    plain = "gm-players-get"
    gm = await _seed_gm(db_session, plain)
    pj = await _seed_pj(db_session, gm_id=gm.id, name="Boromir")
    session = await _seed_session(db_session, gm_id=gm.id)

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            f"/services/jdr/sessions/{session.id}/players",
            json={"pj_ids": [str(pj.id)]},
            headers={"Authorization": f"Bearer {plain}"},
        )
        response = await client.get(
            f"/services/jdr/sessions/{session.id}/players",
            headers={"Authorization": f"Bearer {plain}"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["pj_ids"] == [str(pj.id)]
    assert "updated_at" in body


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


async def test_post_players_422_on_foreign_pj(db_session, make_db_session_dep):
    """Un MJ ne peut mapper que ses propres PJ (FR-012)."""
    plain_a = "gm-a-players-iso"
    plain_b = "gm-b-players-iso"
    gm_a = await _seed_gm(db_session, plain_a)
    gm_b = await _seed_gm(db_session, plain_b)
    pj_b = await _seed_pj(db_session, gm_id=gm_b.id, name="PjDeB")
    session_a = await _seed_session(db_session, gm_id=gm_a.id)

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/services/jdr/sessions/{session_a.id}/players",
            json={"pj_ids": [str(pj_b.id)]},
            headers={"Authorization": f"Bearer {plain_a}"},
        )
    assert response.status_code == 422
    assert response.json()["type"].endswith("/invalid-player-list")


async def test_post_players_409_on_diarised_session(
    db_session, make_db_session_dep
):
    """L'endpoint /players est réservé aux sessions non_diarised."""
    plain = "gm-players-wrong-mode"
    gm = await _seed_gm(db_session, plain)
    pj = await _seed_pj(db_session, gm_id=gm.id, name="X")
    session = await _seed_session(
        db_session, gm_id=gm.id, mode=TranscriptionMode.DIARISED
    )

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/services/jdr/sessions/{session.id}/players",
            json={"pj_ids": [str(pj.id)]},
            headers={"Authorization": f"Bearer {plain}"},
        )
    assert response.status_code == 409
    assert response.json()["type"].endswith("/wrong-mode")


async def test_post_players_404_on_foreign_session(
    db_session, make_db_session_dep
):
    plain_a = "gm-a-players-foreign"
    plain_b = "gm-b-players-foreign"
    gm_a = await _seed_gm(db_session, plain_a)
    gm_b = await _seed_gm(db_session, plain_b)
    pj_a = await _seed_pj(db_session, gm_id=gm_a.id, name="A")
    session_b = await _seed_session(db_session, gm_id=gm_b.id)

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/services/jdr/sessions/{session_b.id}/players",
            json={"pj_ids": [str(pj_a.id)]},
            headers={"Authorization": f"Bearer {plain_a}"},
        )
    assert response.status_code == 404
