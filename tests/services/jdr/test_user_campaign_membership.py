"""Campaign side effects of JDR user management endpoints."""

from collections.abc import Callable
from uuid import UUID

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.services.jdr.auth_router import router as auth_router
from app.services.jdr.campaigns import DEFAULT_CAMPAIGN_ID
from app.services.jdr.db.models import CampaignMember, CampaignRole


def _make_app(make_db_session_dep: Callable[..., object]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    return app


async def _setup_admin(client: AsyncClient):
    response = await client.post(
        "/services/jdr/auth/setup",
        json={"username": "admin", "password": "admin-password"},
    )
    assert response.status_code == 201
    return response.json()


async def test_post_user_creates_one_campaign_membership(
    db_session,
    make_db_session_dep,
):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        response = await client.post(
            "/services/jdr/users",
            json={
                "username": "alice",
                "profile": "user",
                "password": "alice-password",
            },
        )

    assert response.status_code == 201
    user_id = UUID(response.json()["id"])
    rows = list(
        (
            await db_session.scalars(
                select(CampaignMember).where(CampaignMember.user_id == user_id)
            )
        ).all()
    )
    assert len(rows) == 1
    assert rows[0].campaign_id == DEFAULT_CAMPAIGN_ID
    assert rows[0].role == CampaignRole.PLAYER


async def test_get_users_lists_only_active_campaign_members(
    db_session,
    make_db_session_dep,
):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        await client.post(
            "/services/jdr/users",
            json={
                "username": "alice",
                "profile": "user",
                "password": "alice-password",
            },
        )
        response = await client.get("/services/jdr/users")

    assert response.status_code == 200
    assert [item["username"] for item in response.json()["items"]] == [
        "admin",
        "alice",
    ]


async def test_patch_user_syncs_membership_role(db_session, make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        created = await client.post(
            "/services/jdr/users",
            json={
                "username": "alice",
                "profile": "user",
                "password": "alice-password",
            },
        )
        user_id = UUID(created.json()["id"])
        response = await client.patch(
            f"/services/jdr/users/{user_id}",
            json={"profile": "gm"},
        )

    assert response.status_code == 200
    role = await db_session.scalar(
        select(CampaignMember.role).where(CampaignMember.user_id == user_id)
    )
    assert role == CampaignRole.MJ


async def test_delete_user_retains_campaign_membership(db_session, make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        created = await client.post(
            "/services/jdr/users",
            json={
                "username": "alice",
                "profile": "user",
                "password": "alice-password",
            },
        )
        user_id = UUID(created.json()["id"])
        response = await client.delete(f"/services/jdr/users/{user_id}")

    assert response.status_code == 204
    membership = await db_session.scalar(
        select(CampaignMember).where(CampaignMember.user_id == user_id)
    )
    assert membership is not None
