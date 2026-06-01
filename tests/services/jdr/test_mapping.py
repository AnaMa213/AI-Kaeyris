"""US3 / sub-lot 5a — speaker ↔ PJ mapping per session.

PUT /services/jdr/sessions/{id}/mapping accepts a ``{speaker_label →
pj_id}`` dict, validates that every PJ belongs to the current MJ
(422 otherwise) and that the session is in ``state=transcribed``
(409 otherwise). When an existing mapping is replaced, every
``artifacts(kind LIKE 'pov:%')`` row for the session is deleted so
the POVs are regenerated from the new mapping (data-model.md §6
invariant + contracts/rest-api.md §148-169).
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
    Campaign,
    Artifact,
    Pj,
    Role,
    Session,
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
    campaign = await db_session.scalar(select(Campaign).limit(1))
    if campaign is None:
        campaign = Campaign(name="Legacy test campaign", owner_user_id=uuid4())
        db_session.add(campaign)
        await db_session.flush()
    pj = Pj(name=name, owner_gm_key_id=gm_id, campaign_id=campaign.id)
    db_session.add(pj)
    await db_session.commit()
    await db_session.refresh(pj)
    return pj


async def _seed_session(
    db_session,
    *,
    gm_id,
    state: SessionState = SessionState.TRANSCRIBED,
    title: str = "Session de test",
) -> Session:
    campaign = await db_session.scalar(select(Campaign).limit(1))
    if campaign is None:
        campaign = Campaign(name="Legacy test campaign", owner_user_id=uuid4())
        db_session.add(campaign)
        await db_session.flush()
    session = Session(
        title=title,
        recorded_at=datetime.now(UTC),
        gm_key_id=gm_id,
        campaign_id=campaign.id,
        state=state,
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)
    return session


async def _seed_pov_artifact(db_session, *, session_id, pj_id) -> Artifact:
    artifact = Artifact(
        session_id=session_id,
        kind=f"pov:{pj_id}",
        content_json={"text": "pre-existing POV content"},
        model_used="test-model",
    )
    db_session.add(artifact)
    await db_session.commit()
    await db_session.refresh(artifact)
    return artifact


# ---------------------------------------------------------------------------
# PUT /sessions/{id}/mapping — happy paths
# ---------------------------------------------------------------------------


async def test_put_mapping_creates_mapping_returns_200(
    db_session, make_db_session_dep
):
    plain = "gm-map-create"
    gm = await _seed_gm(db_session, plain)
    pj_a = await _seed_pj(db_session, gm_id=gm.id, name="Galadriel")
    pj_b = await _seed_pj(db_session, gm_id=gm.id, name="Aragorn")
    session = await _seed_session(db_session, gm_id=gm.id)

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            f"/services/jdr/sessions/{session.id}/mapping",
            json={
                "mapping": {
                    "speaker_1": str(pj_a.id),
                    "speaker_2": str(pj_b.id),
                }
            },
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == str(session.id)
    assert body["mapping"] == {
        "speaker_1": str(pj_a.id),
        "speaker_2": str(pj_b.id),
    }
    assert "updated_at" in body


async def test_put_mapping_replaces_existing_mapping(
    db_session, make_db_session_dep
):
    """A second PUT fully replaces the first mapping (no merge)."""
    plain = "gm-map-replace"
    gm = await _seed_gm(db_session, plain)
    pj_a = await _seed_pj(db_session, gm_id=gm.id, name="Frodon")
    pj_b = await _seed_pj(db_session, gm_id=gm.id, name="Sam")
    pj_c = await _seed_pj(db_session, gm_id=gm.id, name="Pippin")
    session = await _seed_session(db_session, gm_id=gm.id)

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.put(
            f"/services/jdr/sessions/{session.id}/mapping",
            json={"mapping": {"speaker_1": str(pj_a.id), "speaker_2": str(pj_b.id)}},
            headers={"Authorization": f"Bearer {plain}"},
        )
        assert first.status_code == 200

        second = await client.put(
            f"/services/jdr/sessions/{session.id}/mapping",
            json={"mapping": {"speaker_1": str(pj_c.id)}},
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert second.status_code == 200
    assert second.json()["mapping"] == {"speaker_1": str(pj_c.id)}


# ---------------------------------------------------------------------------
# PUT /sessions/{id}/mapping — validation failures
# ---------------------------------------------------------------------------


async def test_put_mapping_rejects_unknown_pj_id_with_422(
    db_session, make_db_session_dep
):
    plain = "gm-map-unknown"
    gm = await _seed_gm(db_session, plain)
    session = await _seed_session(db_session, gm_id=gm.id)

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            f"/services/jdr/sessions/{session.id}/mapping",
            json={"mapping": {"speaker_1": str(uuid4())}},
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 422
    assert response.json()["type"].endswith("/invalid-mapping")


async def test_put_mapping_rejects_other_gm_pj_with_422(
    db_session, make_db_session_dep
):
    """A MJ cannot map a session to a PJ owned by another MJ (FR-014)."""
    plain_a = "gm-a-iso"
    plain_b = "gm-b-iso"
    gm_a = await _seed_gm(db_session, plain_a)
    gm_b = await _seed_gm(db_session, plain_b)
    pj_of_b = await _seed_pj(db_session, gm_id=gm_b.id, name="PjDeB")
    session_of_a = await _seed_session(db_session, gm_id=gm_a.id)

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            f"/services/jdr/sessions/{session_of_a.id}/mapping",
            json={"mapping": {"speaker_1": str(pj_of_b.id)}},
            headers={"Authorization": f"Bearer {plain_a}"},
        )

    assert response.status_code == 422
    assert response.json()["type"].endswith("/invalid-mapping")


async def test_put_mapping_rejects_session_not_transcribed_with_409(
    db_session, make_db_session_dep
):
    """The session must be in ``state=transcribed`` (rest-api.md §148-167)."""
    plain = "gm-map-state"
    gm = await _seed_gm(db_session, plain)
    pj = await _seed_pj(db_session, gm_id=gm.id, name="EarlyBird")
    session = await _seed_session(
        db_session, gm_id=gm.id, state=SessionState.AUDIO_UPLOADED
    )

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            f"/services/jdr/sessions/{session.id}/mapping",
            json={"mapping": {"speaker_1": str(pj.id)}},
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 409


async def test_put_mapping_on_other_gm_session_returns_404(
    db_session, make_db_session_dep
):
    """A MJ cannot map a session owned by another MJ (FR-013/014)."""
    plain_a = "gm-a-foreign"
    plain_b = "gm-b-foreign"
    gm_a = await _seed_gm(db_session, plain_a)
    gm_b = await _seed_gm(db_session, plain_b)
    pj_of_a = await _seed_pj(db_session, gm_id=gm_a.id, name="PjDeA")
    session_of_b = await _seed_session(db_session, gm_id=gm_b.id)

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            f"/services/jdr/sessions/{session_of_b.id}/mapping",
            json={"mapping": {"speaker_1": str(pj_of_a.id)}},
            headers={"Authorization": f"Bearer {plain_a}"},
        )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# PUT /sessions/{id}/mapping — invalidation of pov:* artefacts (critical)
# ---------------------------------------------------------------------------


async def test_put_mapping_invalidates_existing_pov_artifacts(
    db_session, make_db_session_dep
):
    """Changing the mapping deletes every ``pov:*`` row for the session
    (data-model.md §6, rest-api.md §169 side effect)."""
    plain = "gm-map-invalidate"
    gm = await _seed_gm(db_session, plain)
    pj_a = await _seed_pj(db_session, gm_id=gm.id, name="Aragorn")
    pj_b = await _seed_pj(db_session, gm_id=gm.id, name="Boromir")
    session = await _seed_session(db_session, gm_id=gm.id)

    # Seed two pov:* artefacts AND a narrative artefact — only pov:* must
    # be invalidated; narrative stays.
    await _seed_pov_artifact(db_session, session_id=session.id, pj_id=pj_a.id)
    await _seed_pov_artifact(db_session, session_id=session.id, pj_id=pj_b.id)
    db_session.add(
        Artifact(
            session_id=session.id,
            kind="narrative",
            content_json={"text": "narrative survives"},
            model_used="test",
        )
    )
    await db_session.commit()

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            f"/services/jdr/sessions/{session.id}/mapping",
            json={"mapping": {"speaker_1": str(pj_a.id)}},
            headers={"Authorization": f"Bearer {plain}"},
        )
    assert response.status_code == 200

    # Re-read through the test session to bypass any in-memory caches.
    from sqlalchemy import select

    rows = (
        await db_session.execute(
            select(Artifact).where(Artifact.session_id == session.id)
        )
    ).scalars().all()
    kinds = sorted(r.kind for r in rows)
    assert kinds == ["narrative"], f"pov:* must be deleted, got {kinds}"


# ---------------------------------------------------------------------------
# GET /sessions/{id}/mapping
# ---------------------------------------------------------------------------


async def test_get_mapping_returns_current_mapping(
    db_session, make_db_session_dep
):
    plain = "gm-map-get"
    gm = await _seed_gm(db_session, plain)
    pj = await _seed_pj(db_session, gm_id=gm.id, name="Gandalf")
    session = await _seed_session(db_session, gm_id=gm.id)

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put(
            f"/services/jdr/sessions/{session.id}/mapping",
            json={"mapping": {"speaker_1": str(pj.id)}},
            headers={"Authorization": f"Bearer {plain}"},
        )
        response = await client.get(
            f"/services/jdr/sessions/{session.id}/mapping",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == str(session.id)
    assert body["mapping"] == {"speaker_1": str(pj.id)}
    assert "updated_at" in body


async def test_get_mapping_empty_session_returns_empty_mapping(
    db_session, make_db_session_dep
):
    """A session that has no mapping yet returns 200 + empty dict (resource
    exists, just not configured). Cohérent avec un client UI qui veut
    pré-remplir un formulaire vide."""
    plain = "gm-map-empty"
    gm = await _seed_gm(db_session, plain)
    session = await _seed_session(db_session, gm_id=gm.id)

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{session.id}/mapping",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["mapping"] == {}
    assert body["updated_at"] is None


# ---------------------------------------------------------------------------
# Auth / role
# ---------------------------------------------------------------------------


async def test_mapping_endpoints_reject_player_role(
    db_session, make_db_session_dep
):
    """Only GMs can read/write the mapping (FR-013)."""
    plain = "player-map"
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
        put_resp = await client.put(
            f"/services/jdr/sessions/{uuid4()}/mapping",
            json={"mapping": {}},
            headers={"Authorization": f"Bearer {plain}"},
        )
        get_resp = await client.get(
            f"/services/jdr/sessions/{uuid4()}/mapping",
            headers={"Authorization": f"Bearer {plain}"},
        )
    # Same convention as test_pjs.py: 401 (player key with no pj_id rejected
    # by auth) or 403 (role mismatch).
    assert put_resp.status_code in (401, 403)
    assert get_resp.status_code in (401, 403)
