"""US1 / feature 002 — Endpoint `GET /sessions/{id}/chunks`.

L'endpoint expose la transcription chunked d'une session `non_diarised`.
Réservé à ce mode (409 sur diarised), filtrée par MJ owner (404 sinon).
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import fakeredis
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.redis_client import get_redis
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    Chunk,
    Role,
    Session,
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


async def _seed_session(
    db_session,
    *,
    gm_id,
    mode: TranscriptionMode = TranscriptionMode.NON_DIARISED,
    state: SessionState = SessionState.TRANSCRIBED,
):
    session = Session(
        title=f"Session {uuid4().hex[:6]}",
        recorded_at=datetime.now(UTC),
        gm_key_id=gm_id,
        state=state,
        transcription_mode=mode,
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)
    return session


async def _seed_chunks(db_session, *, session_id, texts):
    for i, text in enumerate(texts):
        db_session.add(Chunk(session_id=session_id, ordre=i, text=text))
    await db_session.commit()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_get_chunks_returns_ordered_items(
    db_session, make_db_session_dep
):
    plain = "gm-chunks-ok"
    gm = await _seed_gm(db_session, plain)
    session = await _seed_session(db_session, gm_id=gm.id)
    await _seed_chunks(
        db_session,
        session_id=session.id,
        texts=["chunk zero", "chunk un", "chunk deux"],
    )

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{session.id}/chunks",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == str(session.id)
    items = body["items"]
    assert len(items) == 3
    # Ordre croissant 0, 1, 2 préservé
    assert [item["ordre"] for item in items] == [0, 1, 2]
    assert [item["text"] for item in items] == [
        "chunk zero",
        "chunk un",
        "chunk deux",
    ]
    # summary_text NE doit PAS apparaître dans la réponse (research.md §5)
    for item in items:
        assert "summary_text" not in item


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


async def test_get_chunks_404_when_no_chunks(
    db_session, make_db_session_dep
):
    """Session non_diarised mais transcription pas encore tournée."""
    plain = "gm-chunks-empty"
    gm = await _seed_gm(db_session, plain)
    session = await _seed_session(
        db_session, gm_id=gm.id, state=SessionState.AUDIO_UPLOADED
    )
    # aucun chunk seedé

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{session.id}/chunks",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 404
    assert response.json()["type"].endswith("/transcription-not-ready")


async def test_get_chunks_409_on_diarised_session(
    db_session, make_db_session_dep
):
    """L'endpoint est réservé au mode non_diarised."""
    plain = "gm-chunks-wrong-mode"
    gm = await _seed_gm(db_session, plain)
    session = await _seed_session(
        db_session, gm_id=gm.id, mode=TranscriptionMode.DIARISED
    )

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{session.id}/chunks",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 409
    assert response.json()["type"].endswith("/wrong-mode")


async def test_get_chunks_404_on_foreign_session(
    db_session, make_db_session_dep
):
    """Un MJ ne peut pas voir les chunks d'un autre MJ."""
    plain_a = "gm-a-chunks-iso"
    plain_b = "gm-b-chunks-iso"
    gm_a = await _seed_gm(db_session, plain_a)
    gm_b = await _seed_gm(db_session, plain_b)
    session_b = await _seed_session(db_session, gm_id=gm_b.id)
    await _seed_chunks(db_session, session_id=session_b.id, texts=["x"])

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{session_b.id}/chunks",
            headers={"Authorization": f"Bearer {plain_a}"},
        )

    assert response.status_code == 404
    _ = gm_a


async def test_get_chunks_requires_gm(db_session, make_db_session_dep):
    plain = "player-no-chunks-access"
    db_session.add(
        ApiKey(
            name=f"player-{uuid4().hex[:8]}",
            hash=PasswordHasher().hash(plain),
            role=Role.PLAYER,
            status=ApiKeyStatus.ACTIVE,
        )
    )
    await db_session.commit()

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{uuid4()}/chunks",
            headers={"Authorization": f"Bearer {plain}"},
        )
    assert response.status_code in (401, 403)
