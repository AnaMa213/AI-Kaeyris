"""BD-7 empty database setup/reseed behavior."""

from collections.abc import Callable

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.models import SystemRole, User
from app.services.jdr.auth_router import router as auth_router
from app.services.jdr.db.models import Campaign, CampaignMember, CampaignRole


def _make_app(make_db_session_dep: Callable[..., object]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    return app


async def test_empty_database_setup_creates_admin_default_campaign_and_gm_membership(
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

    user = (
        await db_session.execute(select(User).where(User.username == "admin"))
    ).scalar_one()
    campaign = (
        await db_session.execute(select(Campaign).where(Campaign.owner_user_id == user.id))
    ).scalar_one()
    membership = (
        await db_session.execute(
            select(CampaignMember).where(
                CampaignMember.user_id == user.id,
                CampaignMember.campaign_id == campaign.id,
            )
        )
    ).scalar_one()

    assert user.system_role == SystemRole.ADMIN
    assert user.default_campaign_id == campaign.id
    assert membership.role == CampaignRole.GM


async def test_no_hardcoded_admin_credential_exists_before_explicit_setup(
    make_db_session_dep,
):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/services/jdr/auth/login",
            json={"username": "admin", "password": "admin"},
        )

    assert response.status_code == 401
