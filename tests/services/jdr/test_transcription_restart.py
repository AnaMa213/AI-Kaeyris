"""Story 7.1 / BD-21 - POST /services/jdr/sessions/{session_id}/transcription/restart.

Re-run transcription on the session's existing audio source (no re-upload).
Allowed from ``transcription_failed`` / ``transcribed``; refused otherwise or
when no usable audio remains.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import fakeredis
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis import Redis
from rq.job import Job as RQJob
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.models import Profile, User, UserStatus
from app.core.redis_client import get_redis
from app.core.users import hash_password
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    AudioSource,
    Campaign,
    Pj,
    Role,
    Session,
    SessionState,
)
from app.services.jdr.router import router as jdr_router


@dataclass
class RestartContext:
    plain_token: str
    gm_key: ApiKey
    campaign_id: object


async def _make_gm_context(
    db_session: AsyncSession,
    *,
    token_prefix: str,
    campaign_name: str,
) -> RestartContext:
    plain = f"{token_prefix}-{uuid4().hex}"
    api_key = ApiKey(
        name=f"{token_prefix}-{uuid4().hex[:8]}",
        hash=PasswordHasher().hash(plain),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
    )
    db_session.add(api_key)
    await db_session.flush()
    user = User(
        username=f"{token_prefix}-{uuid4().hex[:8]}",
        profile=Profile.GM,
        password_hash=hash_password("gm-password"),
        status=UserStatus.ACTIVE,
        api_key_id=api_key.id,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(user)
    await db_session.flush()
    campaign = Campaign(name=campaign_name, owner_user_id=user.id)
    db_session.add(campaign)
    await db_session.commit()
    await db_session.refresh(api_key)
    await db_session.refresh(campaign)
    api_key.test_campaign_id = campaign.id
    return RestartContext(
        plain_token=plain, gm_key=api_key, campaign_id=campaign.id
    )


@pytest_asyncio.fixture
async def restart_ctx(db_session: AsyncSession) -> RestartContext:
    return await _make_gm_context(
        db_session,
        token_prefix="gm-restart",
        campaign_name="Restart campaign",
    )


def _make_jdr_app(
    make_db_session_dep: Callable[..., object],
    redis_client: Redis | None = None,
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(jdr_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = (
        lambda: redis_client or fakeredis.FakeStrictRedis()
    )
    return app


async def _create_session(
    db_session: AsyncSession,
    ctx: RestartContext,
    *,
    state: SessionState,
) -> Session:
    session = Session(
        title="Session to re-transcribe",
        recorded_at=datetime.now(UTC),
        gm_key_id=ctx.gm_key.id,
        campaign_id=ctx.campaign_id,
        state=state,
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)
    return session


async def _attach_audio(
    db_session: AsyncSession,
    session: Session,
    *,
    purged: bool = False,
) -> None:
    audio = AudioSource(
        session_id=session.id,
        path=f"audio/{session.id}/raw.m4a",
        sha256="0" * 64,
        size_bytes=2048,
        duration_seconds=90,
        purged_at=datetime.now(UTC) if purged else None,
    )
    db_session.add(audio)
    await db_session.commit()


async def _create_player_token(
    db_session: AsyncSession, ctx: RestartContext
) -> str:
    pj = Pj(
        name=f"Player PJ {uuid4().hex[:6]}",
        owner_gm_key_id=ctx.gm_key.id,
        campaign_id=ctx.campaign_id,
    )
    db_session.add(pj)
    await db_session.flush()
    plain = f"player-restart-{uuid4().hex}"
    player_key = ApiKey(
        name=f"player-restart-{uuid4().hex[:8]}",
        hash=PasswordHasher().hash(plain),
        role=Role.PLAYER,
        status=ApiKeyStatus.ACTIVE,
        pj_id=pj.id,
    )
    db_session.add(player_key)
    await db_session.commit()
    return plain


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _restart_url(session_id: object) -> str:
    return f"/services/jdr/sessions/{session_id}/transcription/restart"


async def test_restart_from_failed_reenqueues(
    restart_ctx, db_session, make_db_session_dep
):
    session = await _create_session(
        db_session, restart_ctx, state=SessionState.TRANSCRIPTION_FAILED
    )
    await _attach_audio(db_session, session)
    session_id = session.id
    redis_client = fakeredis.FakeStrictRedis()
    app = _make_jdr_app(make_db_session_dep, redis_client)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            _restart_url(session_id),
            headers=_auth_headers(restart_ctx.plain_token),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(session_id)
    assert body["state"] == "audio_uploaded"
    assert body["current_job_id"] is not None
    # A fresh RQ job was enqueued for the re-transcription.
    assert RQJob.exists(body["current_job_id"], connection=redis_client)

    db_session.expire_all()
    refreshed = await db_session.get(Session, session_id)
    assert refreshed.state is SessionState.AUDIO_UPLOADED
    assert refreshed.current_job_id == body["current_job_id"]


async def test_restart_from_transcribed_reenqueues(
    restart_ctx, db_session, make_db_session_dep
):
    session = await _create_session(
        db_session, restart_ctx, state=SessionState.TRANSCRIBED
    )
    await _attach_audio(db_session, session)
    session_id = session.id
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            _restart_url(session_id),
            headers=_auth_headers(restart_ctx.plain_token),
        )

    assert response.status_code == 200
    assert response.json()["state"] == "audio_uploaded"


async def test_restart_without_audio_returns_409(
    restart_ctx, db_session, make_db_session_dep
):
    session = await _create_session(
        db_session, restart_ctx, state=SessionState.TRANSCRIPTION_FAILED
    )
    session_id = session.id
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            _restart_url(session_id),
            headers=_auth_headers(restart_ctx.plain_token),
        )

    assert response.status_code == 409
    assert response.json()["type"].endswith("/no-audio-to-transcribe")


async def test_restart_with_purged_audio_returns_409(
    restart_ctx, db_session, make_db_session_dep
):
    session = await _create_session(
        db_session, restart_ctx, state=SessionState.TRANSCRIPTION_FAILED
    )
    await _attach_audio(db_session, session, purged=True)
    session_id = session.id
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            _restart_url(session_id),
            headers=_auth_headers(restart_ctx.plain_token),
        )

    assert response.status_code == 409
    assert response.json()["type"].endswith("/no-audio-to-transcribe")


async def test_restart_wrong_state_returns_409(
    restart_ctx, db_session, make_db_session_dep
):
    session = await _create_session(
        db_session, restart_ctx, state=SessionState.TRANSCRIBING
    )
    await _attach_audio(db_session, session)
    session_id = session.id
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            _restart_url(session_id),
            headers=_auth_headers(restart_ctx.plain_token),
        )

    assert response.status_code == 409
    assert response.json()["type"].endswith("/transcription-restart-not-allowed")
    db_session.expire_all()
    refreshed = await db_session.get(Session, session_id)
    assert refreshed.state is SessionState.TRANSCRIBING


async def test_restart_unknown_session_returns_404(
    restart_ctx, make_db_session_dep
):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            _restart_url(uuid4()),
            headers=_auth_headers(restart_ctx.plain_token),
        )

    assert response.status_code == 404
    assert response.json()["type"].endswith("/session-not-found")


async def test_restart_rejects_player_role(
    restart_ctx, db_session, make_db_session_dep
):
    session = await _create_session(
        db_session, restart_ctx, state=SessionState.TRANSCRIPTION_FAILED
    )
    await _attach_audio(db_session, session)
    player_token = await _create_player_token(db_session, restart_ctx)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            _restart_url(session.id),
            headers=_auth_headers(player_token),
        )

    assert response.status_code == 403


async def test_restart_requires_authentication(
    restart_ctx, db_session, make_db_session_dep
):
    session = await _create_session(
        db_session, restart_ctx, state=SessionState.TRANSCRIPTION_FAILED
    )
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(_restart_url(session.id))

    assert response.status_code == 401
