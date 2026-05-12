"""US1 — GET /services/jdr/jobs/{job_id} (job status projection).

RQ is the source of truth for live job state. The route fetches the
job from Redis, derives ``kind`` from the function name and ``session_id``
from the args, enforces cross-tenant isolation (404), and exposes a
``JobOut`` projection. RQ's TTL (24h success / 7d failure) is plenty for
US1 — a persistent jdr_jobs projection is left for a later jalon.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import fakeredis
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.redis_client import get_redis
from app.jobs import enqueue_job, get_default_queue
from app.jobs.jdr import generate_narrative_job, transcribe_session_job
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    Role,
    Session,
    SessionState,
)
from app.services.jdr.router import router as jdr_router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class JobsTestContext:
    plain_token: str
    gm_key_id: UUID
    session_id: UUID
    redis_client: fakeredis.FakeStrictRedis
    sessionmaker: async_sessionmaker


@pytest_asyncio.fixture
async def ctx(db_engine: AsyncEngine, monkeypatch) -> JobsTestContext:
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)

    plain = "gm-jobs-token"
    session_id = uuid4()
    async with sm() as setup:
        gm = ApiKey(
            name=f"gm-jobs-{uuid4().hex[:8]}",
            hash=PasswordHasher().hash(plain),
            role=Role.GM,
            status=ApiKeyStatus.ACTIVE,
        )
        setup.add(gm)
        await setup.flush()
        setup.add(
            Session(
                id=session_id,
                title="Jobs route test",
                recorded_at=datetime.now(UTC),
                gm_key_id=gm.id,
                state=SessionState.TRANSCRIBED,
            )
        )
        await setup.commit()
        await setup.refresh(gm)
        gm_id = gm.id

    return JobsTestContext(
        plain_token=plain,
        gm_key_id=gm_id,
        session_id=session_id,
        redis_client=fakeredis.FakeStrictRedis(),
        sessionmaker=sm,
    )


def _make_jdr_app(
    make_db_session_dep: Callable[..., Any],
    redis_client: fakeredis.FakeStrictRedis,
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(jdr_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = lambda: redis_client
    return app


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_get_job_for_freshly_enqueued_transcription(
    ctx, make_db_session_dep
):
    queue = get_default_queue(ctx.redis_client)
    job = enqueue_job(queue, transcribe_session_job, ctx.session_id)

    app = _make_jdr_app(make_db_session_dep, ctx.redis_client)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/jobs/{job.id}",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == job.id
    assert body["kind"] == "transcription"
    assert body["session_id"] == str(ctx.session_id)
    assert body["status"] == "queued"
    assert body["queued_at"] is not None
    # Not yet running / done -> these are None
    assert body["started_at"] is None
    assert body["ended_at"] is None
    assert body["failure_reason"] is None


async def test_get_job_for_narrative_kind(ctx, make_db_session_dep):
    queue = get_default_queue(ctx.redis_client)
    job = enqueue_job(queue, generate_narrative_job, ctx.session_id)

    app = _make_jdr_app(make_db_session_dep, ctx.redis_client)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/jobs/{job.id}",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "narrative"
    assert body["session_id"] == str(ctx.session_id)


# ---------------------------------------------------------------------------
# Not found / forbidden cases
# ---------------------------------------------------------------------------


async def test_get_unknown_job_returns_404(ctx, make_db_session_dep):
    app = _make_jdr_app(make_db_session_dep, ctx.redis_client)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/services/jdr/jobs/this-job-does-not-exist",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert response.status_code == 404
    body = response.json()
    assert body["type"].endswith("/job-not-found")


async def test_get_job_cross_tenant_returns_404(ctx, make_db_session_dep):
    """A second GM cannot peek at someone else's enqueued job."""
    queue = get_default_queue(ctx.redis_client)
    job = enqueue_job(queue, transcribe_session_job, ctx.session_id)

    plain_b = "another-gm-jobs-token"
    async with ctx.sessionmaker() as db:
        db.add(
            ApiKey(
                name="another-gm-jobs",
                hash=PasswordHasher().hash(plain_b),
                role=Role.GM,
                status=ApiKeyStatus.ACTIVE,
            )
        )
        await db.commit()

    app = _make_jdr_app(make_db_session_dep, ctx.redis_client)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/jobs/{job.id}",
            headers={"Authorization": f"Bearer {plain_b}"},
        )

    # 404 — never 403; leaks less about what jobs exist.
    assert response.status_code == 404
    body = response.json()
    assert body["type"].endswith("/job-not-found")


async def test_get_job_requires_authentication(ctx, make_db_session_dep):
    queue = get_default_queue(ctx.redis_client)
    job = enqueue_job(queue, transcribe_session_job, ctx.session_id)

    app = _make_jdr_app(make_db_session_dep, ctx.redis_client)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/services/jdr/jobs/{job.id}")

    assert response.status_code == 401


async def test_get_job_rejects_player_role(ctx, make_db_session_dep):
    """Player keys cannot poll GM jobs."""
    queue = get_default_queue(ctx.redis_client)
    job = enqueue_job(queue, transcribe_session_job, ctx.session_id)

    plain_player = "player-jobs-token"
    async with ctx.sessionmaker() as db:
        from app.services.jdr.db.models import Pj

        pj = Pj(name="Aragorn", owner_gm_key_id=ctx.gm_key_id)
        db.add(pj)
        await db.flush()
        db.add(
            ApiKey(
                name="player-jobs",
                hash=PasswordHasher().hash(plain_player),
                role=Role.PLAYER,
                status=ApiKeyStatus.ACTIVE,
                pj_id=pj.id,
            )
        )
        await db.commit()

    app = _make_jdr_app(make_db_session_dep, ctx.redis_client)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/jobs/{job.id}",
            headers={"Authorization": f"Bearer {plain_player}"},
        )

    assert response.status_code == 403
