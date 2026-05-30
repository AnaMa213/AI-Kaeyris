"""Campaign scoping on JDR root resources."""

from collections.abc import Callable
from uuid import UUID, uuid4

import fakeredis
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis import Redis

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.models import User
from app.core.redis_client import get_redis
from app.services.jdr.auth_router import router as auth_router
from app.services.jdr.db.models import ApiKey
from app.services.jdr.router import router as jdr_router
from tests.services.jdr.campaign_factories import make_campaign, make_pj, make_session


def _make_app(
    make_db_session_dep: Callable[..., object],
    redis_client: Redis,
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.include_router(jdr_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = lambda: redis_client
    return app


async def _setup_admin(client: AsyncClient):
    response = await client.post(
        "/services/jdr/auth/setup",
        json={"username": "admin", "password": "admin-password"},
    )
    assert response.status_code == 201
    return response.json()


async def test_session_create_list_and_read_are_campaign_scoped(
    db_session,
    make_db_session_dep,
):
    app = _make_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin = await _setup_admin(client)
        created = await client.post(
            "/services/jdr/sessions",
            json={
                "title": "Visible",
                "recorded_at": "2026-05-30T20:30:00+00:00",
            },
        )
        assert created.status_code == 201

        user = await db_session.get(User, UUID(admin["id"]))
        api_key = await db_session.get(ApiKey, user.api_key_id)
        other_campaign = await make_campaign(
            db_session,
            owner=user,
            campaign_id=uuid4(),
            name="Other",
        )
        foreign_session = await make_session(
            db_session,
            owner_key=api_key,
            campaign=other_campaign,
            title="Hidden",
        )
        await db_session.commit()

        listed = await client.get("/services/jdr/sessions")
        foreign = await client.get(f"/services/jdr/sessions/{foreign_session.id}")

    assert listed.status_code == 200
    assert [item["title"] for item in listed.json()["items"]] == ["Visible"]
    assert foreign.status_code == 404


async def test_pj_create_list_and_validation_are_campaign_scoped(
    db_session,
    make_db_session_dep,
):
    app = _make_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin = await _setup_admin(client)
        created = await client.post("/services/jdr/pjs", json={"name": "Visible PJ"})
        assert created.status_code == 201

        user = await db_session.get(User, UUID(admin["id"]))
        api_key = await db_session.get(ApiKey, user.api_key_id)
        other_campaign = await make_campaign(
            db_session,
            owner=user,
            campaign_id=uuid4(),
            name="Other",
        )
        await make_pj(
            db_session,
            owner_key=api_key,
            campaign=other_campaign,
            name="Hidden PJ",
        )
        await db_session.commit()

        listed = await client.get("/services/jdr/pjs")

    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()["items"]] == ["Visible PJ"]


async def test_frontend_supplied_campaign_id_is_rejected(make_db_session_dep):
    app = _make_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        session_response = await client.post(
            "/services/jdr/sessions",
            json={
                "title": "Invalid",
                "recorded_at": "2026-05-30T20:30:00+00:00",
                "campaign_id": str(uuid4()),
            },
        )
        pj_response = await client.post(
            "/services/jdr/pjs",
            json={"name": "Invalid", "campaign_id": str(uuid4())},
        )

    assert session_response.status_code == 422
    assert pj_response.status_code == 422
