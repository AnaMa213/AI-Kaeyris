"""Campaign isolation tests for existing JDR endpoints."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

import fakeredis
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.models import User
from app.core.redis_client import get_redis
from app.services.jdr.auth_router import router as auth_router
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    Artifact,
    Campaign,
    Pj,
    Role,
    Session,
    SessionPjMapping,
    SessionState,
)
from app.services.jdr.router import router as jdr_router


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


async def _setup_admin(client: AsyncClient, db_session: AsyncSession) -> tuple[User, Campaign]:
    response = await client.post(
        "/services/jdr/auth/setup",
        json={"username": "admin", "password": "admin-password"},
    )
    assert response.status_code == 201
    admin = await db_session.scalar(select(User).where(User.username == "admin"))
    campaign = await db_session.scalar(select(Campaign))
    assert admin is not None
    assert campaign is not None
    return admin, campaign


async def test_sessions_are_created_listed_and_loaded_in_active_campaign_only(
    db_session,
    make_db_session_dep,
):
    app = _make_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin, campaign_a = await _setup_admin(client, db_session)
        created = await client.post(
            "/services/jdr/sessions",
            json={
                "title": "Visible",
                "recorded_at": "2026-05-31T20:30:00+00:00",
                "campaign_id": str(campaign_a.id),
            },
        )
        assert created.status_code == 201
        campaign_b = Campaign(
            name="Hidden",
            owner_user_id=admin.id,
            created_at=datetime.now(UTC),
        )
        db_session.add(campaign_b)
        await db_session.flush()
        hidden = Session(
            title="Hidden",
            recorded_at=datetime.now(UTC),
            gm_key_id=admin.api_key_id,
            campaign_id=campaign_b.id,
        )
        db_session.add(hidden)
        await db_session.commit()

        listed = await client.get(f"/services/jdr/sessions?campaign_id={campaign_a.id}")
        hidden_detail = await client.get(f"/services/jdr/sessions/{hidden.id}")
        hidden_patch = await client.patch(
            f"/services/jdr/sessions/{hidden.id}",
            json={"title": "Leak?"},
        )

    assert created.json()["title"] == "Visible"
    visible_row = await db_session.get(Session, UUID(created.json()["id"]))
    assert visible_row is not None
    assert visible_row.campaign_id == campaign_a.id
    assert [item["title"] for item in listed.json()["items"]] == ["Visible"]
    assert hidden_detail.status_code == 403
    assert hidden_patch.status_code == 404


async def test_pjs_are_created_and_listed_in_active_campaign_only(
    db_session,
    make_db_session_dep,
):
    app = _make_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin, campaign_a = await _setup_admin(client, db_session)
        created = await client.post("/services/jdr/pjs", json={"name": "Visible"})
        assert created.status_code == 201
        campaign_b = Campaign(
            name="Hidden",
            owner_user_id=admin.id,
            created_at=datetime.now(UTC),
        )
        db_session.add(campaign_b)
        await db_session.flush()
        hidden = Pj(
            name="Hidden",
            owner_gm_key_id=admin.api_key_id,
            campaign_id=campaign_b.id,
        )
        db_session.add(hidden)
        await db_session.commit()

        listed = await client.get("/services/jdr/pjs")

    visible_row = await db_session.get(Pj, UUID(created.json()["id"]))
    assert visible_row is not None
    assert visible_row.campaign_id == campaign_a.id
    assert [item["name"] for item in listed.json()["items"]] == ["Visible"]


async def test_mapping_rejects_pj_from_another_campaign(
    db_session,
    make_db_session_dep,
):
    app = _make_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin, campaign_a = await _setup_admin(client, db_session)
        session_response = await client.post(
            "/services/jdr/sessions",
            json={
                "title": "Visible",
                "recorded_at": "2026-05-31T20:30:00+00:00",
                "campaign_id": str(campaign_a.id),
            },
        )
        session = await db_session.get(Session, UUID(session_response.json()["id"]))
        session.state = SessionState.TRANSCRIBED
        campaign_b = Campaign(
            name="Other",
            owner_user_id=admin.id,
            created_at=datetime.now(UTC),
        )
        db_session.add(campaign_b)
        await db_session.flush()
        foreign_pj = Pj(
            name="Foreign",
            owner_gm_key_id=admin.api_key_id,
            campaign_id=campaign_b.id,
        )
        db_session.add(foreign_pj)
        await db_session.commit()

        response = await client.put(
            f"/services/jdr/sessions/{session.id}/mapping",
            json={"mapping": {"speaker_1": str(foreign_pj.id)}},
        )

    assert response.status_code == 422


async def test_player_session_list_is_bound_to_player_campaign(
    db_session,
    make_db_session_dep,
):
    player_token = "player-token"
    app = _make_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin, campaign_a = await _setup_admin(client, db_session)
        pj = Pj(name="Aelar", owner_gm_key_id=admin.api_key_id, campaign_id=campaign_a.id)
        db_session.add(pj)
        await db_session.flush()
        player_key = ApiKey(
            name="player",
            hash=PasswordHasher().hash(player_token),
            role=Role.PLAYER,
            status=ApiKeyStatus.ACTIVE,
            pj_id=pj.id,
        )
        visible = Session(
            title="Visible",
            recorded_at=datetime.now(UTC),
            gm_key_id=admin.api_key_id,
            campaign_id=campaign_a.id,
        )
        campaign_b = Campaign(
            name="Other",
            owner_user_id=admin.id,
            created_at=datetime.now(UTC),
        )
        db_session.add_all([player_key, visible, campaign_b])
        await db_session.flush()
        hidden = Session(
            title="Hidden",
            recorded_at=datetime.now(UTC),
            gm_key_id=admin.api_key_id,
            campaign_id=campaign_b.id,
        )
        db_session.add(hidden)
        await db_session.flush()
        db_session.add_all(
            [
                SessionPjMapping(
                    session_id=visible.id,
                    speaker_label="speaker_1",
                    pj_id=pj.id,
                ),
                SessionPjMapping(
                    session_id=hidden.id,
                    speaker_label="speaker_1",
                    pj_id=pj.id,
                ),
                Artifact(
                    session_id=hidden.id,
                    kind="narrative",
                    content_json={"text": "secret"},
                    model_used="test",
                    generated_at=datetime.now(UTC),
                ),
            ]
        )
        await db_session.commit()

        response = await client.get(
            "/services/jdr/me/sessions",
            headers={"Authorization": f"Bearer {player_token}"},
        )
        hidden_narrative = await client.get(
            f"/services/jdr/me/sessions/{hidden.id}/narrative",
            headers={"Authorization": f"Bearer {player_token}"},
        )

    assert response.status_code == 200
    assert [item["title"] for item in response.json()["items"]] == ["Visible"]
    assert hidden_narrative.status_code == 403
