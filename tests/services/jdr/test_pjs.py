"""US3 / sub-lot 5a — PJ CRUD.

POST /pjs creates a PJ owned by the current MJ. GET /pjs lists only the
PJs of the current MJ (FR-014 isolation discipline). The
``(owner_gm_key_id, name)`` uniqueness constraint translates to 409
``duplicate-pj``.
"""

from collections.abc import Callable
from typing import Any
from uuid import uuid4

import fakeredis
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.redis_client import get_redis
from app.core.config import settings
from app.core.models import Profile
from app.services.jdr.db.models import ApiKey, ApiKeyStatus, Role
from app.services.jdr.router import router as jdr_router
from tests.services.jdr.campaign_fixtures import (
    make_campaign,
    make_membership,
    make_user,
    make_web_session,
)


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


async def _seed_web_gm(db_session, *, username: str = "gm"):
    user = await make_user(db_session, username=username, profile=Profile.GM)
    campaign = await make_campaign(db_session, owner=user, name=f"{username}-campaign")
    await make_membership(db_session, user=user, campaign=campaign)
    user.default_campaign_id = campaign.id
    token = await make_web_session(db_session, user=user)
    return user, campaign, token


# ---------------------------------------------------------------------------
# POST /pjs
# ---------------------------------------------------------------------------


async def test_post_pj_returns_201_with_pj_payload(
    db_session, make_db_session_dep
):
    _user, campaign, token = await _seed_web_gm(db_session)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(settings.SESSION_COOKIE_NAME, token)
        response = await client.post(
            "/services/jdr/pjs",
            json={"name": "Aragorn"},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Aragorn"
    assert body["campaign_id"] == str(campaign.id)
    assert body["user_id"] is None
    assert "id" in body
    assert "created_at" in body


async def test_post_pj_rejects_duplicate_name_for_same_gm(
    db_session, make_db_session_dep
):
    """``(owner_gm_key_id, name)`` is unique — second insert -> 409."""
    _user, _campaign, token = await _seed_web_gm(db_session)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(settings.SESSION_COOKIE_NAME, token)
        first = await client.post(
            "/services/jdr/pjs",
            json={"name": "Galadriel"},
        )
        assert first.status_code == 201
        second = await client.post(
            "/services/jdr/pjs",
            json={"name": "Galadriel"},
        )

    assert second.status_code == 409
    assert second.json()["type"].endswith("/duplicate-pj")


async def test_post_pj_allows_same_name_for_different_gms(
    db_session, make_db_session_dep
):
    """The uniqueness is *per MJ* — two MJs can both own a PJ named 'Frodon'."""
    _user_a, _campaign_a, token_a = await _seed_web_gm(db_session, username="gm-a")
    _user_b, _campaign_b, token_b = await _seed_web_gm(db_session, username="gm-b")
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(settings.SESSION_COOKIE_NAME, token_a)
        a = await client.post(
            "/services/jdr/pjs",
            json={"name": "Frodon"},
        )
        client.cookies.set(settings.SESSION_COOKIE_NAME, token_b)
        b = await client.post(
            "/services/jdr/pjs",
            json={"name": "Frodon"},
        )

    assert a.status_code == 201
    assert b.status_code == 201
    assert a.json()["id"] != b.json()["id"]


async def test_post_pj_rejects_empty_name_with_422(
    db_session, make_db_session_dep
):
    plain = "gm-empty"
    await _seed_gm(db_session, plain)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/services/jdr/pjs",
            json={"name": ""},
            headers={"Authorization": f"Bearer {plain}"},
        )
    assert response.status_code == 422


async def test_post_pj_rejects_player_role_with_403(
    db_session, make_db_session_dep
):
    """Only GMs can manage their PJs (FR-013)."""
    plain = "player-pj"
    # A player key with no pj_id (US4 hasn't run yet, so it will be rejected
    # before role check — but we just want a non-GM here).
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
        response = await client.post(
            "/services/jdr/pjs",
            json={"name": "wannabe"},
            headers={"Authorization": f"Bearer {plain}"},
        )
    # Either 401 (player key with no pj_id rejected by auth) or 403 (role mismatch).
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /pjs
# ---------------------------------------------------------------------------


async def test_get_pjs_lists_only_current_mj_pjs(
    db_session, make_db_session_dep
):
    _user_a, _campaign_a, token_a = await _seed_web_gm(db_session, username="gm-list-a")
    _user_b, _campaign_b, token_b = await _seed_web_gm(db_session, username="gm-list-b")
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(settings.SESSION_COOKIE_NAME, token_a)
        await client.post(
            "/services/jdr/pjs",
            json={"name": "PJ-de-A-1"},
        )
        await client.post(
            "/services/jdr/pjs",
            json={"name": "PJ-de-A-2"},
        )
        client.cookies.set(settings.SESSION_COOKIE_NAME, token_b)
        await client.post(
            "/services/jdr/pjs",
            json={"name": "PJ-de-B"},
        )

        client.cookies.set(settings.SESSION_COOKIE_NAME, token_a)
        list_a = await client.get(
            "/services/jdr/pjs",
        )
        client.cookies.set(settings.SESSION_COOKIE_NAME, token_b)
        list_b = await client.get(
            "/services/jdr/pjs",
        )

    assert list_a.status_code == 200
    names_a = sorted(item["name"] for item in list_a.json()["items"])
    assert names_a == ["PJ-de-A-1", "PJ-de-A-2"]

    assert list_b.status_code == 200
    names_b = [item["name"] for item in list_b.json()["items"]]
    assert names_b == ["PJ-de-B"]


async def test_get_pjs_empty_returns_empty_page(
    db_session, make_db_session_dep
):
    plain = "gm-empty-list"
    await _seed_gm(db_session, plain)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/services/jdr/pjs",
            headers={"Authorization": f"Bearer {plain}"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_pj_endpoints_require_auth(make_db_session_dep):
    """No Bearer token -> 401 from require_api_key."""
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        post_resp = await client.post(
            "/services/jdr/pjs", json={"name": "ghost"}
        )
        get_resp = await client.get("/services/jdr/pjs")
    assert post_resp.status_code in (401, 403)
    assert get_resp.status_code in (401, 403)
