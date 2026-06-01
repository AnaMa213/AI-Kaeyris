"""US4 — strict isolation of player keys (FR-014, the critical security test).

A player key must NEVER be able to:
- See another player's POV (or even probe its existence).
- Mutate any GM-owned resource (PJ creation, mapping, artefact triggers).
- See sessions or PJs that don't belong to its scope.

This file is the FR-014 acceptance gate. Failures here are blocking.
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
    Campaign,
    Artifact,
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


async def _seed_two_players(db_session):
    """GM + 2 PJ + 2 players (one per PJ) + Session(transcribed) + mapping for pj_a only."""
    hasher = PasswordHasher()
    gm_plain = "gm-access-iso"
    player_a_plain = "player-a-token"
    player_b_plain = "player-b-token"

    gm = ApiKey(
        name=f"gm-{uuid4().hex[:8]}",
        hash=hasher.hash(gm_plain),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
    )
    db_session.add(gm)
    await db_session.commit()
    await db_session.refresh(gm)

    campaign = Campaign(name="Access campaign", owner_user_id=uuid4())
    db_session.add(campaign)
    await db_session.flush()

    pj_a = Pj(name="PjA", owner_gm_key_id=gm.id, campaign_id=campaign.id)
    pj_b = Pj(name="PjB", owner_gm_key_id=gm.id, campaign_id=campaign.id)
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
    player_b = ApiKey(
        name=f"player-b-{uuid4().hex[:8]}",
        hash=hasher.hash(player_b_plain),
        role=Role.PLAYER,
        status=ApiKeyStatus.ACTIVE,
        pj_id=pj_b.id,
    )
    db_session.add(player_a)
    db_session.add(player_b)
    await db_session.commit()

    session = Session(
        title="Access-iso session",
        recorded_at=datetime.now(UTC),
        gm_key_id=gm.id,
        campaign_id=campaign.id,
        state=SessionState.TRANSCRIBED,
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)

    # Only pj_a is mapped to this session. pj_b is NOT.
    db_session.add(
        SessionPjMapping(
            session_id=session.id,
            speaker_label="speaker_1",
            pj_id=pj_a.id,
        )
    )
    # Seed POVs for both PJ (only pj_a is mapped though).
    db_session.add(
        Artifact(
            session_id=session.id,
            kind=f"pov:{pj_a.id}",
            content_json={"text": "POV de A — secret pour A uniquement."},
            model_used="test",
        )
    )
    db_session.add(
        Artifact(
            session_id=session.id,
            kind=f"pov:{pj_b.id}",
            content_json={"text": "POV de B — ne devrait jamais fuiter."},
            model_used="test",
        )
    )
    db_session.add(
        Artifact(
            session_id=session.id,
            kind="narrative",
            content_json={"text": "Récit narratif global."},
            model_used="test",
        )
    )
    await db_session.commit()

    return {
        "gm_plain": gm_plain,
        "player_a_plain": player_a_plain,
        "player_b_plain": player_b_plain,
        "pj_a_id": pj_a.id,
        "pj_b_id": pj_b.id,
        "session_id": session.id,
    }


# ---------------------------------------------------------------------------
# Read access — a player only sees what their own PJ is mapped to
# ---------------------------------------------------------------------------


async def test_player_b_cannot_read_session_where_their_pj_is_not_mapped(
    db_session, make_db_session_dep
):
    """Player B's pj_b is not mapped on this session → 403 on narrative/pov."""
    fx = await _seed_two_players(db_session)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        narrative_resp = await client.get(
            f"/services/jdr/me/sessions/{fx['session_id']}/narrative",
            headers={"Authorization": f"Bearer {fx['player_b_plain']}"},
        )
        pov_resp = await client.get(
            f"/services/jdr/me/sessions/{fx['session_id']}/pov",
            headers={"Authorization": f"Bearer {fx['player_b_plain']}"},
        )
    # Either 403 (resource exists but not mine) or 404 (we hide existence).
    # The spec is explicit: 403 (see rest-api.md §318-334).
    assert narrative_resp.status_code == 403
    assert pov_resp.status_code == 403


async def test_player_a_sees_their_own_pov_not_other_pj_pov(
    db_session, make_db_session_dep
):
    """The /me/pov endpoint always returns the *current player's* pov, never
    an arbitrary pj_id passed by the caller. There is no path param for
    the PJ on /me routes — that's the design intent."""
    fx = await _seed_two_players(db_session)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        pov_resp = await client.get(
            f"/services/jdr/me/sessions/{fx['session_id']}/pov",
            headers={"Authorization": f"Bearer {fx['player_a_plain']}"},
        )
    assert pov_resp.status_code == 200
    body = pov_resp.json()
    assert body["pj_id"] == str(fx["pj_a_id"])
    assert "POV de A" in body["text"]
    # Player B's content must not leak.
    assert "POV de B" not in body["text"]


# ---------------------------------------------------------------------------
# Write access — a player CANNOT mutate GM-owned resources
# ---------------------------------------------------------------------------


async def test_player_cannot_create_pj(db_session, make_db_session_dep):
    fx = await _seed_two_players(db_session)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/services/jdr/pjs",
            json={"name": "intrus"},
            headers={"Authorization": f"Bearer {fx['player_a_plain']}"},
        )
    assert response.status_code == 403


async def test_player_cannot_put_mapping(db_session, make_db_session_dep):
    fx = await _seed_two_players(db_session)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            f"/services/jdr/sessions/{fx['session_id']}/mapping",
            json={"mapping": {"speaker_1": str(fx["pj_a_id"])}},
            headers={"Authorization": f"Bearer {fx['player_a_plain']}"},
        )
    assert response.status_code == 403


async def test_player_cannot_post_artifacts(db_session, make_db_session_dep):
    fx = await _seed_two_players(db_session)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        narrative_resp = await client.post(
            f"/services/jdr/sessions/{fx['session_id']}/artifacts/narrative",
            headers={"Authorization": f"Bearer {fx['player_a_plain']}"},
        )
        povs_resp = await client.post(
            f"/services/jdr/sessions/{fx['session_id']}/artifacts/povs",
            headers={"Authorization": f"Bearer {fx['player_a_plain']}"},
        )
    assert narrative_resp.status_code == 403
    assert povs_resp.status_code == 403


async def test_player_cannot_enroll_other_players(
    db_session, make_db_session_dep
):
    fx = await _seed_two_players(db_session)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/services/jdr/players",
            json={"name": "alt", "pj_id": str(fx["pj_a_id"])},
            headers={"Authorization": f"Bearer {fx['player_a_plain']}"},
        )
    assert response.status_code == 403
