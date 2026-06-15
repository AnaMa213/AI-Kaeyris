"""AC10 - POST /services/jdr/sessions/{session_id}/transcription/recover.

A transcription worker sets the session to ``transcribing`` before the heavy
lifting and only reaches ``transcribed`` / ``transcription_failed`` at the end.
If the worker dies mid-run the session is wedged in ``transcribing`` forever
while its RQ job is gone from Redis. This endpoint performs the failed
transition the worker never reached so the GM can replace audio / delete.
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
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.models import Profile, User, UserStatus
from app.core.redis_client import get_redis
from app.core.users import hash_password
from app.jobs import enqueue_job, get_default_queue
from app.jobs.jdr import transcribe_session_job
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    Job,
    JobKind,
    JobStatus,
    Pj,
    Role,
    Session,
    SessionState,
)
from app.services.jdr.router import router as jdr_router


@dataclass
class RecoverContext:
    plain_token: str
    gm_key: ApiKey
    campaign_id: object


async def _make_gm_context(
    db_session: AsyncSession,
    *,
    token_prefix: str,
    campaign_name: str,
) -> RecoverContext:
    from app.services.jdr.db.models import Campaign

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
    return RecoverContext(
        plain_token=plain, gm_key=api_key, campaign_id=campaign.id
    )


@pytest_asyncio.fixture
async def recover_ctx(db_session: AsyncSession) -> RecoverContext:
    return await _make_gm_context(
        db_session,
        token_prefix="gm-recover",
        campaign_name="Recover campaign",
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
    ctx: RecoverContext,
    *,
    title: str = "Stuck session",
    state: SessionState = SessionState.TRANSCRIBING,
) -> Session:
    session = Session(
        title=title,
        recorded_at=datetime.now(UTC),
        gm_key_id=ctx.gm_key.id,
        campaign_id=ctx.campaign_id,
        state=state,
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)
    return session


async def _attach_dead_job(
    db_session: AsyncSession,
    session: Session,
    *,
    kind: JobKind = JobKind.TRANSCRIPTION,
    sql_status: JobStatus = JobStatus.RUNNING,
) -> str:
    """Attach a current_job_id whose RQ job no longer exists in Redis."""
    job_id = uuid4().hex
    job = Job(
        id=job_id,
        kind=kind,
        session_id=session.id,
        status=sql_status,
        queued_at=datetime.now(UTC),
    )
    db_session.add(job)
    await db_session.flush()
    session.current_job_id = job_id
    await db_session.commit()
    return job_id


async def _attach_active_rq_job(
    db_session: AsyncSession,
    session: Session,
    *,
    redis_client: fakeredis.FakeStrictRedis,
) -> str:
    queue = get_default_queue(redis_client)
    rq_job = enqueue_job(queue, transcribe_session_job, session.id)
    job = Job(
        id=rq_job.id,
        kind=JobKind.TRANSCRIPTION,
        session_id=session.id,
        status=JobStatus.RUNNING,
        queued_at=datetime.now(UTC),
    )
    db_session.add(job)
    await db_session.flush()
    session.current_job_id = rq_job.id
    await db_session.commit()
    return rq_job.id


async def _create_player_token(
    db_session: AsyncSession, ctx: RecoverContext
) -> str:
    pj = Pj(
        name=f"Player PJ {uuid4().hex[:6]}",
        owner_gm_key_id=ctx.gm_key.id,
        campaign_id=ctx.campaign_id,
    )
    db_session.add(pj)
    await db_session.flush()
    plain = f"player-recover-{uuid4().hex}"
    player_key = ApiKey(
        name=f"player-recover-{uuid4().hex[:8]}",
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


def _recover_url(session_id: object) -> str:
    return f"/services/jdr/sessions/{session_id}/transcription/recover"


async def test_recover_stuck_session_returns_200_and_marks_failed(
    recover_ctx, db_session, make_db_session_dep
):
    session = await _create_session(db_session, recover_ctx)
    job_id = await _attach_dead_job(db_session, session)
    session_id = session.id
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            _recover_url(session_id),
            headers=_auth_headers(recover_ctx.plain_token),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(session_id)
    assert body["state"] == "transcription_failed"

    db_session.expire_all()
    refreshed = await db_session.get(Session, session_id)
    assert refreshed.state is SessionState.TRANSCRIPTION_FAILED
    job = await db_session.get(Job, job_id)
    assert job.status is JobStatus.FAILED
    assert job.failure_reason is not None


async def test_recover_without_current_job_marks_failed(
    recover_ctx, db_session, make_db_session_dep
):
    # Worker crashed before set_current_job_id ran.
    session = await _create_session(db_session, recover_ctx)
    session_id = session.id
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            _recover_url(session_id),
            headers=_auth_headers(recover_ctx.plain_token),
        )

    assert response.status_code == 200
    assert response.json()["state"] == "transcription_failed"


async def test_recover_then_delete_succeeds(
    recover_ctx, db_session, make_db_session_dep
):
    session = await _create_session(db_session, recover_ctx)
    await _attach_dead_job(db_session, session)
    session_id = session.id
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        recovered = await client.post(
            _recover_url(session_id),
            headers=_auth_headers(recover_ctx.plain_token),
        )
        deleted = await client.delete(
            f"/services/jdr/sessions/{session_id}",
            headers=_auth_headers(recover_ctx.plain_token),
        )

    assert recovered.status_code == 200
    assert deleted.status_code == 204
    db_session.expire_all()
    assert await db_session.get(Session, session_id) is None


async def test_recover_still_active_job_returns_409(
    recover_ctx, db_session, make_db_session_dep
):
    session = await _create_session(db_session, recover_ctx)
    redis_client = fakeredis.FakeStrictRedis()
    await _attach_active_rq_job(db_session, session, redis_client=redis_client)
    session_id = session.id
    app = _make_jdr_app(make_db_session_dep, redis_client)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            _recover_url(session_id),
            headers=_auth_headers(recover_ctx.plain_token),
        )

    assert response.status_code == 409
    assert response.json()["type"].endswith("/transcription-still-active")
    db_session.expire_all()
    refreshed = await db_session.get(Session, session_id)
    assert refreshed.state is SessionState.TRANSCRIBING


async def test_recover_not_transcribing_returns_409(
    recover_ctx, db_session, make_db_session_dep
):
    session = await _create_session(
        db_session, recover_ctx, state=SessionState.TRANSCRIBED
    )
    session_id = session.id
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            _recover_url(session_id),
            headers=_auth_headers(recover_ctx.plain_token),
        )

    assert response.status_code == 409
    assert response.json()["type"].endswith("/transcription-not-stuck")
    db_session.expire_all()
    refreshed = await db_session.get(Session, session_id)
    assert refreshed.state is SessionState.TRANSCRIBED


async def test_recover_unknown_session_returns_404(
    recover_ctx, make_db_session_dep
):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            _recover_url(uuid4()),
            headers=_auth_headers(recover_ctx.plain_token),
        )

    assert response.status_code == 404
    assert response.json()["type"].endswith("/session-not-found")


async def test_recover_cross_gm_returns_404(
    recover_ctx, db_session, make_db_session_dep
):
    session = await _create_session(db_session, recover_ctx)
    await _attach_dead_job(db_session, session)
    other_ctx = await _make_gm_context(
        db_session,
        token_prefix="other-recover",
        campaign_name="Other recover campaign",
    )
    session_id = session.id
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            _recover_url(session_id),
            headers=_auth_headers(other_ctx.plain_token),
        )

    assert response.status_code == 404
    assert response.json()["type"].endswith("/session-not-found")


async def test_recover_requires_authentication(
    recover_ctx, db_session, make_db_session_dep
):
    session = await _create_session(db_session, recover_ctx)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(_recover_url(session.id))

    assert response.status_code == 401


async def test_recover_rejects_player_role(
    recover_ctx, db_session, make_db_session_dep
):
    session = await _create_session(db_session, recover_ctx)
    player_token = await _create_player_token(db_session, recover_ctx)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            _recover_url(session.id),
            headers=_auth_headers(player_token),
        )

    assert response.status_code == 403
