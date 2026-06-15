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
from app.core.models import Profile, User
from app.core.redis_client import get_redis
from app.services.jdr.auth_router import router as auth_router
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    Artifact,
    Campaign,
    CampaignRole,
    Pj,
    Role,
    Session,
    SessionPjMapping,
    SessionState,
)
from app.services.jdr.router import router as jdr_router
from tests.services.jdr.campaign_fixtures import (
    make_campaign,
    make_membership,
    make_pj,
    make_session,
    make_user,
    make_web_session,
)


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


async def test_sessions_require_membership_for_foreign_campaign_rows(
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


async def test_gm_session_actions_use_session_campaign_not_default_campaign(
    tmp_path,
    db_session,
    make_db_session_dep,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    gm = await make_user(db_session, username="gm", profile=Profile.GM)
    campaign_a = await make_campaign(db_session, owner=gm, name="A")
    campaign_b = await make_campaign(db_session, owner=gm, name="B")
    await make_membership(
        db_session, user=gm, campaign=campaign_a, role=CampaignRole.GM
    )
    await make_membership(
        db_session, user=gm, campaign=campaign_b, role=CampaignRole.GM
    )
    assert gm.default_campaign_id == campaign_a.id

    patch_session = await make_session(
        db_session, owner=gm, campaign=campaign_b, title="Patch me"
    )
    delete_session = await make_session(
        db_session, owner=gm, campaign=campaign_b, title="Delete me"
    )
    audio_session = await make_session(
        db_session, owner=gm, campaign=campaign_b, title="Audio me"
    )
    mapping_session = await make_session(
        db_session, owner=gm, campaign=campaign_b, title="Map me"
    )
    mapping_session.state = SessionState.TRANSCRIBED
    narrative_session = await make_session(
        db_session, owner=gm, campaign=campaign_b, title="Narrate me"
    )
    narrative_session.state = SessionState.TRANSCRIBED
    pj = await make_pj(db_session, owner=gm, campaign=campaign_b, name="Aelar")
    token = await make_web_session(db_session, user=gm)

    app = _make_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)
    audio_bytes = b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 256

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("session", token)
        patched = await client.patch(
            f"/services/jdr/sessions/{patch_session.id}",
            json={"title": "Patched outside active campaign"},
        )
        uploaded = await client.post(
            f"/services/jdr/sessions/{audio_session.id}/audio",
            files={"audio": ("session.m4a", audio_bytes, "audio/mp4")},
        )
        fetched_audio = await client.get(
            f"/services/jdr/sessions/{audio_session.id}/audio"
        )
        mapped = await client.put(
            f"/services/jdr/sessions/{mapping_session.id}/mapping",
            json={"mapping": {"speaker_1": str(pj.id)}},
        )
        queued = await client.post(
            f"/services/jdr/sessions/{narrative_session.id}/artifacts/narrative"
        )
        job_id = queued.json()["id"] if queued.status_code == 202 else "missing"
        job_status = await client.get(f"/services/jdr/jobs/{job_id}")
        deleted = await client.delete(
            f"/services/jdr/sessions/{delete_session.id}"
        )

    assert patched.status_code == 200, patched.text
    assert patched.json()["title"] == "Patched outside active campaign"
    assert uploaded.status_code == 202, uploaded.text
    assert fetched_audio.status_code == 200, fetched_audio.text
    assert fetched_audio.content == audio_bytes
    assert mapped.status_code == 200, mapped.text
    assert mapped.json()["mapping"] == {"speaker_1": str(pj.id)}
    assert queued.status_code == 202, queued.text
    assert job_status.status_code == 200, job_status.text
    assert deleted.status_code == 204, deleted.text


async def test_pj_campaign_member_cannot_mutate_session(
    db_session,
    make_db_session_dep,
):
    gm = await make_user(db_session, username="gm-owner", profile=Profile.GM)
    player = await make_user(
        db_session, username="campaign-player", profile=Profile.USER
    )
    campaign = await make_campaign(db_session, owner=gm, name="Shared")
    await make_membership(db_session, user=gm, campaign=campaign, role=CampaignRole.GM)
    await make_membership(
        db_session, user=player, campaign=campaign, role=CampaignRole.PJ
    )
    session = await make_session(db_session, owner=gm, campaign=campaign)
    token = await make_web_session(db_session, user=player)
    app = _make_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("session", token)
        response = await client.patch(
            f"/services/jdr/sessions/{session.id}",
            json={"title": "Nope"},
        )

    assert response.status_code == 404
    assert response.json()["type"].endswith("/session-not-found")


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
