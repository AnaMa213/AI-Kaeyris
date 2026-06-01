"""First-run setup endpoint tests."""

from collections.abc import Callable

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.models import User
from app.services.jdr.auth_router import router as auth_router
from app.services.jdr.db.models import Campaign, CampaignMember, CampaignRole


def _make_app(make_db_session_dep: Callable[..., object]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    return app


async def test_setup_status_is_required_on_empty_users_table(make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/services/jdr/auth/setup/status")

    assert response.status_code == 200
    assert response.json() == {"required": True}


async def test_setup_creates_first_gm_and_then_closes(make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/services/jdr/auth/setup",
            json={"username": "admin", "password": "chosen-password"},
        )
        status_after = await client.get("/services/jdr/auth/setup/status")
        second = await client.post(
            "/services/jdr/auth/setup",
            json={"username": "other", "password": "chosen-password"},
        )

    assert created.status_code == 201
    assert created.json()["username"] == "admin"
    assert created.json()["profile"] == "gm"
    assert "password_hash" not in created.text
    assert "session=" in created.headers["set-cookie"]
    assert status_after.json() == {"required": False}
    assert second.status_code == 409


async def test_setup_creates_default_campaign_membership_and_user_default(
    db_session,
    make_db_session_dep,
):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/services/jdr/auth/setup",
            json={"username": "admin", "password": "chosen-password"},
        )

    assert response.status_code == 201
    user = await db_session.scalar(select(User).where(User.username == "admin"))
    campaign = await db_session.scalar(select(Campaign))
    assert user is not None
    assert campaign is not None
    assert user.default_campaign_id == campaign.id
    membership = await db_session.get(
        CampaignMember,
        {"user_id": user.id, "campaign_id": campaign.id},
    )
    assert membership is not None
    assert membership.role == CampaignRole.GM
