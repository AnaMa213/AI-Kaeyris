"""US3 / sub-lot 5a — PJ CRUD.

POST /pjs creates a PJ owned by the current MJ. GET /pjs lists only the
PJs of the current MJ (FR-014 isolation discipline). The
``(campaign_id, name)`` uniqueness constraint (per-campaign, BD-7 scoping)
translates to 409 ``duplicate-pj``.
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


async def _create_pj(client: AsyncClient, *, name: str, user_id: str | None = None):
    payload: dict[str, str] = {"name": name}
    if user_id is not None:
        payload["user_id"] = user_id
    response = await client.post("/services/jdr/pjs", json=payload)
    assert response.status_code == 201
    return response.json()


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


async def test_post_pj_rejects_duplicate_name_in_same_campaign(
    db_session, make_db_session_dep
):
    """``(campaign_id, name)`` is unique — second insert in the same campaign
    -> 409."""
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


async def test_post_pj_allows_same_name_in_another_campaign_same_gm(
    db_session, make_db_session_dep
):
    """BD fix: PJ name uniqueness is per-campaign, not per-MJ. The same GM can
    reuse a name in a different one of their campaigns; the same name in the
    SAME campaign is still rejected."""
    user, _campaign1, token = await _seed_web_gm(db_session, username="gm-multi")
    # A second campaign owned + GM'd by the same user.
    campaign2 = await make_campaign(
        db_session, owner=user, name="gm-multi-campaign-2"
    )
    await make_membership(db_session, user=user, campaign=campaign2)
    await db_session.commit()

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(settings.SESSION_COOKIE_NAME, token)
        # Same name in the default campaign...
        first = await client.post(
            "/services/jdr/pjs", json={"name": "Boromir"}
        )
        assert first.status_code == 201
        # ...and in the second campaign → now allowed.
        cross = await client.post(
            "/services/jdr/pjs",
            json={"name": "Boromir", "campaign_id": str(campaign2.id)},
        )
        assert cross.status_code == 201, cross.text
        assert cross.json()["campaign_id"] == str(campaign2.id)
        # But a duplicate within the second campaign is still rejected.
        dup = await client.post(
            "/services/jdr/pjs",
            json={"name": "Boromir", "campaign_id": str(campaign2.id)},
        )

    assert dup.status_code == 409
    assert dup.json()["type"].endswith("/duplicate-pj")


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


async def test_post_pj_rejects_unknown_user_id_with_invalid_user(
    db_session, make_db_session_dep
):
    _user, _campaign, token = await _seed_web_gm(db_session, username="gm-bad-user")
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(settings.SESSION_COOKIE_NAME, token)
        response = await client.post(
            "/services/jdr/pjs",
            json={"name": "Boromir", "user_id": str(uuid4())},
        )

    assert response.status_code == 422
    assert response.json()["type"].endswith("/invalid-user")


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


# ---------------------------------------------------------------------------
# PATCH /pjs/{pj_id}
# ---------------------------------------------------------------------------


async def test_patch_pj_renames_owned_pj(db_session, make_db_session_dep):
    _user, _campaign, token = await _seed_web_gm(db_session, username="gm-rename")
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(settings.SESSION_COOKIE_NAME, token)
        pj = await _create_pj(client, name="Aragorn")
        response = await client.patch(
            f"/services/jdr/pjs/{pj['id']}",
            json={"name": "Grand-Pas"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == pj["id"]
    assert body["name"] == "Grand-Pas"
    assert body["campaign_id"] == pj["campaign_id"]
    assert body["user_id"] is None


async def test_patch_pj_renames_only_target_pj(db_session, make_db_session_dep):
    _user, _campaign, token = await _seed_web_gm(
        db_session, username="gm-rename-target"
    )
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(settings.SESSION_COOKIE_NAME, token)
        target = await _create_pj(client, name="Legolas")
        other = await _create_pj(client, name="Gimli")
        response = await client.patch(
            f"/services/jdr/pjs/{target['id']}",
            json={"name": "Legolas Vertefeuille"},
        )
        listing = await client.get("/services/jdr/pjs")

    assert response.status_code == 200
    names_by_id = {item["id"]: item["name"] for item in listing.json()["items"]}
    assert names_by_id[target["id"]] == "Legolas Vertefeuille"
    assert names_by_id[other["id"]] == "Gimli"


async def test_patch_pj_rejects_duplicate_name_for_same_gm(
    db_session, make_db_session_dep
):
    _user, _campaign, token = await _seed_web_gm(db_session, username="gm-patch-dup")
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(settings.SESSION_COOKIE_NAME, token)
        target = await _create_pj(client, name="Merry")
        await _create_pj(client, name="Pippin")
        response = await client.patch(
            f"/services/jdr/pjs/{target['id']}",
            json={"name": "Pippin"},
        )

    assert response.status_code == 409
    assert response.json()["type"].endswith("/duplicate-pj")


async def test_patch_pj_links_existing_user(db_session, make_db_session_dep):
    _gm, _campaign, token = await _seed_web_gm(db_session, username="gm-link")
    player = await make_user(db_session, username="player-link", profile=Profile.USER)
    await db_session.commit()
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(settings.SESSION_COOKIE_NAME, token)
        pj = await _create_pj(client, name="Eowyn")
        response = await client.patch(
            f"/services/jdr/pjs/{pj['id']}",
            json={"user_id": str(player.id)},
        )

    assert response.status_code == 200
    assert response.json()["user_id"] == str(player.id)


async def test_patch_pj_clears_user_link_with_explicit_null(
    db_session, make_db_session_dep
):
    _gm, _campaign, token = await _seed_web_gm(db_session, username="gm-unlink")
    player = await make_user(db_session, username="player-unlink", profile=Profile.USER)
    await db_session.commit()
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(settings.SESSION_COOKIE_NAME, token)
        pj = await _create_pj(client, name="Eomer", user_id=str(player.id))
        response = await client.patch(
            f"/services/jdr/pjs/{pj['id']}",
            json={"user_id": None},
        )

    assert response.status_code == 200
    assert response.json()["user_id"] is None


async def test_patch_pj_omitted_user_id_preserves_existing_link(
    db_session, make_db_session_dep
):
    _gm, _campaign, token = await _seed_web_gm(db_session, username="gm-preserve")
    player = await make_user(db_session, username="player-preserve", profile=Profile.USER)
    await db_session.commit()
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(settings.SESSION_COOKIE_NAME, token)
        pj = await _create_pj(client, name="Sam", user_id=str(player.id))
        response = await client.patch(
            f"/services/jdr/pjs/{pj['id']}",
            json={"name": "Samsagace"},
        )

    assert response.status_code == 200
    assert response.json()["name"] == "Samsagace"
    assert response.json()["user_id"] == str(player.id)


async def test_patch_pj_rejects_unknown_user_id_with_invalid_user(
    db_session, make_db_session_dep
):
    _gm, _campaign, token = await _seed_web_gm(db_session, username="gm-patch-bad-user")
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(settings.SESSION_COOKIE_NAME, token)
        pj = await _create_pj(client, name="Faramir")
        response = await client.patch(
            f"/services/jdr/pjs/{pj['id']}",
            json={"user_id": str(uuid4())},
        )

    assert response.status_code == 422
    assert response.json()["type"].endswith("/invalid-user")


async def test_patch_pj_empty_body_is_noop(db_session, make_db_session_dep):
    _gm, _campaign, token = await _seed_web_gm(db_session, username="gm-noop")
    player = await make_user(db_session, username="player-noop", profile=Profile.USER)
    await db_session.commit()
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(settings.SESSION_COOKIE_NAME, token)
        pj = await _create_pj(client, name="Bilbon", user_id=str(player.id))
        response = await client.patch(f"/services/jdr/pjs/{pj['id']}", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == pj["id"]
    assert body["name"] == "Bilbon"
    assert body["user_id"] == str(player.id)


async def test_patch_pj_foreign_pj_returns_404(db_session, make_db_session_dep):
    _user_a, _campaign_a, token_a = await _seed_web_gm(
        db_session, username="gm-foreign-a"
    )
    _user_b, _campaign_b, token_b = await _seed_web_gm(
        db_session, username="gm-foreign-b"
    )
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(settings.SESSION_COOKIE_NAME, token_a)
        pj = await _create_pj(client, name="Theoden")
        client.cookies.set(settings.SESSION_COOKIE_NAME, token_b)
        response = await client.patch(
            f"/services/jdr/pjs/{pj['id']}",
            json={"name": "Intrusion"},
        )

    assert response.status_code == 404
    assert response.json()["type"].endswith("/pj-not-found")


async def test_patch_pj_foreign_attempt_leaves_original_unchanged(
    db_session, make_db_session_dep
):
    _user_a, _campaign_a, token_a = await _seed_web_gm(
        db_session, username="gm-unchanged-a"
    )
    _user_b, _campaign_b, token_b = await _seed_web_gm(
        db_session, username="gm-unchanged-b"
    )
    player = await make_user(db_session, username="player-unchanged", profile=Profile.USER)
    await db_session.commit()
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(settings.SESSION_COOKIE_NAME, token_a)
        pj = await _create_pj(client, name="Elrond", user_id=str(player.id))
        client.cookies.set(settings.SESSION_COOKIE_NAME, token_b)
        forbidden = await client.patch(
            f"/services/jdr/pjs/{pj['id']}",
            json={"name": "Intrusion", "user_id": None},
        )
        client.cookies.set(settings.SESSION_COOKIE_NAME, token_a)
        listing = await client.get("/services/jdr/pjs")

    assert forbidden.status_code == 404
    unchanged = next(item for item in listing.json()["items"] if item["id"] == pj["id"])
    assert unchanged["name"] == "Elrond"
    assert unchanged["user_id"] == str(player.id)


async def test_patch_pj_rejects_player_role_and_missing_auth(
    db_session, make_db_session_dep
):
    _user, _campaign, token = await _seed_web_gm(db_session, username="gm-patch-auth")
    plain = "player-patch-pj"
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
        client.cookies.set(settings.SESSION_COOKIE_NAME, token)
        pj = await _create_pj(client, name="Auth target")
        client.cookies.clear()
        missing_auth = await client.patch(
            f"/services/jdr/pjs/{pj['id']}",
            json={"name": "Nope"},
        )
        player_resp = await client.patch(
            f"/services/jdr/pjs/{pj['id']}",
            json={"name": "Nope"},
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert missing_auth.status_code in (401, 403)
    assert player_resp.status_code in (401, 403)


async def test_patch_pj_is_present_in_openapi(make_db_session_dep):
    app = _make_jdr_app(make_db_session_dep)
    schema = app.openapi()

    path = schema["paths"]["/services/jdr/pjs/{pj_id}"]
    assert "patch" in path
    patch = path["patch"]
    assert patch["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/PjOut")
    body_ref = patch["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert body_ref.endswith("/PjUpdate")
    pj_update = schema["components"]["schemas"]["PjUpdate"]
    assert set(pj_update["properties"]) == {"name", "user_id"}
