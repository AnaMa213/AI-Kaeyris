"""US4 / sub-lot 6 — player enrolment and revocation.

POST /services/jdr/players (require_gm) creates a player key bound to
one PJ owned by the current MJ. The plaintext token is returned once.
The stored row holds an Argon2 hash, never the plaintext.

DELETE /services/jdr/players/{player_id} (require_gm) revokes the key:
the next request with that token receives 401.
"""

from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

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
)
from app.services.jdr.router import router as jdr_router


def _make_jdr_app(make_db_session_dep: Callable[..., Any]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(jdr_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = lambda: fakeredis.FakeStrictRedis()
    return app


async def _seed_gm_and_pj(db_session, plain_token: str, pj_name: str = "Aragorn"):
    gm = ApiKey(
        name=f"gm-{uuid4().hex[:8]}",
        hash=PasswordHasher().hash(plain_token),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
    )
    db_session.add(gm)
    await db_session.commit()
    await db_session.refresh(gm)
    pj = Pj(name=pj_name, owner_gm_key_id=gm.id)
    db_session.add(pj)
    await db_session.commit()
    await db_session.refresh(pj)
    return gm, pj


async def test_post_player_returns_token_once_and_stores_hash(
    db_session, make_db_session_dep
):
    gm_plain = "gm-enroll"
    _, pj = await _seed_gm_and_pj(db_session, gm_plain)

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/services/jdr/players",
            json={"name": "joueur-aragorn", "pj_id": str(pj.id)},
            headers={"Authorization": f"Bearer {gm_plain}"},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "joueur-aragorn"
    assert body["pj_id"] == str(pj.id)
    plaintext_token = body["token"]
    assert isinstance(plaintext_token, str) and len(plaintext_token) >= 24, (
        "Token must be a non-trivial random string."
    )

    # The stored hash is an Argon2 string — never the plaintext.
    row = await db_session.scalar(
        select(ApiKey).where(ApiKey.id == UUID(body["id"]))
    )
    assert row is not None
    assert row.role == Role.PLAYER
    assert row.pj_id == pj.id
    assert row.status == ApiKeyStatus.ACTIVE
    assert row.hash.startswith("$argon2id$")
    assert plaintext_token not in row.hash


async def test_post_player_rejects_foreign_pj_with_422(
    db_session, make_db_session_dep
):
    """A MJ can only enroll a player on one of *their* PJs (FR-014)."""
    gm_a_plain = "gm-a-enroll"
    gm_b_plain = "gm-b-enroll"
    _, pj_a = await _seed_gm_and_pj(db_session, gm_a_plain, pj_name="PjDeA")
    _, pj_b = await _seed_gm_and_pj(db_session, gm_b_plain, pj_name="PjDeB")

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/services/jdr/players",
            json={"name": "intrus", "pj_id": str(pj_b.id)},
            headers={"Authorization": f"Bearer {gm_a_plain}"},
        )

    assert response.status_code == 422
    assert response.json()["type"].endswith("/invalid-player")
    # PJ row of B must NOT have been used to enroll a player.
    _ = pj_a  # kept for symmetry, no assertion on it


async def test_delete_player_revokes_access(
    db_session, make_db_session_dep
):
    """After DELETE, the plaintext token immediately stops authenticating."""
    gm_plain = "gm-revoke"
    _, pj = await _seed_gm_and_pj(db_session, gm_plain)

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Enroll
        enroll = await client.post(
            "/services/jdr/players",
            json={"name": "joueur-revocable", "pj_id": str(pj.id)},
            headers={"Authorization": f"Bearer {gm_plain}"},
        )
        assert enroll.status_code == 201
        player_id = enroll.json()["id"]
        player_token = enroll.json()["token"]

        # Player token authenticates GET /me
        me_before = await client.get(
            "/services/jdr/me",
            headers={"Authorization": f"Bearer {player_token}"},
        )
        assert me_before.status_code == 200

        # GM revokes
        del_resp = await client.delete(
            f"/services/jdr/players/{player_id}",
            headers={"Authorization": f"Bearer {gm_plain}"},
        )
        assert del_resp.status_code in (200, 204)

        # Player token now rejected
        me_after = await client.get(
            "/services/jdr/me",
            headers={"Authorization": f"Bearer {player_token}"},
        )
        assert me_after.status_code == 401


async def test_player_endpoints_reject_no_auth(make_db_session_dep):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        post_resp = await client.post(
            "/services/jdr/players",
            json={"name": "ghost", "pj_id": str(uuid4())},
        )
        del_resp = await client.delete(f"/services/jdr/players/{uuid4()}")
    assert post_resp.status_code in (401, 403)
    assert del_resp.status_code in (401, 403)
