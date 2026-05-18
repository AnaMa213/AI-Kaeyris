"""US4 — GET /me + GET /me/sessions filters by the current player's PJ."""

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
    Pj,
    Role,
    Session,
    SessionPjMapping,
    SessionState,
)
from app.services.jdr.router import router as jdr_router


def _make_jdr_app(make_db_session_dep: Callable[..., Any]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(jdr_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = lambda: fakeredis.FakeStrictRedis()
    return app


async def _seed_listing_fixture(db_session):
    """GM + 2 PJ (A, B) + 1 player on PJ A.

    Session 1 maps speaker_1 -> pj_a (visible to A).
    Session 2 maps speaker_1 -> pj_b (NOT visible to A).
    Session 3 has no mapping (not visible to anyone).
    """
    hasher = PasswordHasher()
    gm_plain = "gm-listing"
    player_a_plain = "player-a-listing"

    gm = ApiKey(
        name=f"gm-{uuid4().hex[:8]}",
        hash=hasher.hash(gm_plain),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
    )
    db_session.add(gm)
    await db_session.commit()
    await db_session.refresh(gm)

    pj_a = Pj(name="Aragorn", owner_gm_key_id=gm.id)
    pj_b = Pj(name="Boromir", owner_gm_key_id=gm.id)
    db_session.add(pj_a)
    db_session.add(pj_b)
    await db_session.commit()
    await db_session.refresh(pj_a)
    await db_session.refresh(pj_b)

    player_a = ApiKey(
        name=f"player-a-{uuid4().hex[:8]}",
        hash=hasher.hash(player_a_plain),
        role=Role.PLAYER,
        status=ApiKeyStatus.ACTIVE,
        pj_id=pj_a.id,
    )
    db_session.add(player_a)
    await db_session.commit()

    s1 = Session(
        title="Session-vue-par-A",
        recorded_at=datetime.now(UTC),
        gm_key_id=gm.id,
        state=SessionState.TRANSCRIBED,
    )
    s2 = Session(
        title="Session-pour-B-seulement",
        recorded_at=datetime.now(UTC),
        gm_key_id=gm.id,
        state=SessionState.TRANSCRIBED,
    )
    s3 = Session(
        title="Session-sans-mapping",
        recorded_at=datetime.now(UTC),
        gm_key_id=gm.id,
        state=SessionState.TRANSCRIBED,
    )
    db_session.add(s1)
    db_session.add(s2)
    db_session.add(s3)
    await db_session.commit()
    await db_session.refresh(s1)
    await db_session.refresh(s2)
    await db_session.refresh(s3)

    db_session.add(SessionPjMapping(session_id=s1.id, speaker_label="speaker_1", pj_id=pj_a.id))
    db_session.add(SessionPjMapping(session_id=s2.id, speaker_label="speaker_1", pj_id=pj_b.id))
    await db_session.commit()

    return {
        "player_a_plain": player_a_plain,
        "pj_a_id": pj_a.id,
        "pj_a_name": pj_a.name,
        "session_visible_id": s1.id,
        "session_other_id": s2.id,
        "session_unmapped_id": s3.id,
    }


async def test_get_me_returns_profile(db_session, make_db_session_dep):
    fx = await _seed_listing_fixture(db_session)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/services/jdr/me",
            headers={"Authorization": f"Bearer {fx['player_a_plain']}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert "name" in body
    assert body["pj"]["id"] == str(fx["pj_a_id"])
    assert body["pj"]["name"] == fx["pj_a_name"]


async def test_get_me_sessions_lists_only_sessions_where_my_pj_is_mapped(
    db_session, make_db_session_dep
):
    fx = await _seed_listing_fixture(db_session)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/services/jdr/me/sessions",
            headers={"Authorization": f"Bearer {fx['player_a_plain']}"},
        )

    assert response.status_code == 200
    items = response.json()["items"]
    ids = [item["session_id"] for item in items]
    assert ids == [str(fx["session_visible_id"])]
    # The other-PJ session and the unmapped session must NOT appear.
    assert str(fx["session_other_id"]) not in ids
    assert str(fx["session_unmapped_id"]) not in ids
