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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.redis_client import get_redis
from rq.job import Job, JobStatus as RQJobStatus

from app.jobs import enqueue_job, get_default_queue
from app.jobs.jdr import (
    generate_narrative_job,
    generate_summary_job,
    transcribe_session_job,
)
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    Campaign,
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
        campaign = Campaign(name="Jobs route campaign", owner_user_id=uuid4())
        setup.add(campaign)
        await setup.flush()
        setup.add(
            Session(
                id=session_id,
                title="Jobs route test",
                recorded_at=datetime.now(UTC),
                gm_key_id=gm.id,
                campaign_id=campaign.id,
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


def _set_job_meta(redis_client: fakeredis.FakeStrictRedis, job_id: str, **meta: Any) -> None:
    """Simulate worker-emitted RQ progress metadata for a job.

    The worker writes ``phase``/``progress_percent`` onto ``job.meta`` via
    ``save_meta()``; the route reads it back. Without a live worker we set
    the same fields directly so the route projection can be asserted.
    """
    job = Job.fetch(job_id, connection=redis_client)
    job.meta.update(meta)
    job.save_meta()


def _mark_job_failed(
    redis_client: fakeredis.FakeStrictRedis, job_id: str, exc_info: str
) -> None:
    job = Job.fetch(job_id, connection=redis_client)
    job.set_status(RQJobStatus.FAILED)
    job._exc_info = exc_info
    job.save()


# ---------------------------------------------------------------------------
# Schema / contract (BD-10 — Foundational)
# ---------------------------------------------------------------------------


def test_jobout_schema_exposes_nullable_progress_fields():
    """BD-10 contract: ``phase`` is a nullable closed enum and
    ``progress_percent`` is a nullable 0..100 integer in the JSON Schema
    (so the regenerated OpenAPI lets the frontend generate typed clients).
    """
    from app.services.jdr.schemas import JobOut

    schema = JobOut.model_json_schema()
    props = schema["properties"]

    assert "phase" in props
    assert "progress_percent" in props

    # phase: closed enum {reducing, transcribing, done, failed} + null.
    phase_variants = props["phase"]["anyOf"]
    enum_values = next(v["enum"] for v in phase_variants if "enum" in v)
    assert set(enum_values) == {"reducing", "transcribing", "done", "failed"}
    assert any(v.get("type") == "null" for v in phase_variants)

    # progress_percent: integer bounded 0..100, nullable.
    pct_variants = props["progress_percent"]["anyOf"]
    int_variant = next(v for v in pct_variants if v.get("type") == "integer")
    assert int_variant["minimum"] == 0
    assert int_variant["maximum"] == 100
    assert any(v.get("type") == "null" for v in pct_variants)


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
    # BD-10: a queued job has no progress metadata yet — the fields are
    # null, never a synthesised phase="queued" or progress_percent=0 (US2).
    assert body["phase"] is None
    assert body["progress_percent"] is None


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
# US1 — real transcription progress projection (BD-10)
# ---------------------------------------------------------------------------


async def test_get_job_for_summary_kind(ctx, make_db_session_dep):
    queue = get_default_queue(ctx.redis_client)
    job = enqueue_job(queue, generate_summary_job, ctx.session_id)

    app = _make_jdr_app(make_db_session_dep, ctx.redis_client)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/jobs/{job.id}",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "summary"
    assert body["session_id"] == str(ctx.session_id)
    assert body["status"] == "queued"
    assert body["failure_reason"] is None


async def test_get_job_running_transcription_exposes_progress(
    ctx, make_db_session_dep
):
    """A running transcription with metadata exposes phase=transcribing and
    an in-flight 0..99 progress percent."""
    queue = get_default_queue(ctx.redis_client)
    job = enqueue_job(queue, transcribe_session_job, ctx.session_id)
    _set_job_meta(
        ctx.redis_client, job.id, phase="transcribing", progress_percent=42
    )

    app = _make_jdr_app(make_db_session_dep, ctx.redis_client)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/jobs/{job.id}",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["phase"] == "transcribing"
    assert body["progress_percent"] == 42
    assert 0 <= body["progress_percent"] <= 99


async def test_get_job_done_transcription_reports_100(
    ctx, make_db_session_dep
):
    """A successful transcription exposes phase=done and progress=100."""
    queue = get_default_queue(ctx.redis_client)
    job = enqueue_job(queue, transcribe_session_job, ctx.session_id)
    _set_job_meta(
        ctx.redis_client, job.id, phase="done", progress_percent=100
    )

    app = _make_jdr_app(make_db_session_dep, ctx.redis_client)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/jobs/{job.id}",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["phase"] == "done"
    assert body["progress_percent"] == 100


# ---------------------------------------------------------------------------
# US2 — best-effort fallback: bad metadata never breaks a valid job (BD-10)
# ---------------------------------------------------------------------------


async def test_get_job_malformed_progress_falls_back_to_null(
    ctx, make_db_session_dep
):
    """Out-of-domain phase + out-of-range percent must not 500: the route
    returns 200 with null progress fields and an unchanged main status."""
    queue = get_default_queue(ctx.redis_client)
    job = enqueue_job(queue, transcribe_session_job, ctx.session_id)
    _set_job_meta(
        ctx.redis_client, job.id, phase="bogus-phase", progress_percent=150
    )

    app = _make_jdr_app(make_db_session_dep, ctx.redis_client)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/jobs/{job.id}",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["phase"] is None
    assert body["progress_percent"] is None
    assert body["status"] == "queued"


async def test_get_job_non_integer_progress_falls_back_to_null(
    ctx, make_db_session_dep
):
    """A non-integer percent (e.g. a stray string) is ignored, not echoed."""
    queue = get_default_queue(ctx.redis_client)
    job = enqueue_job(queue, transcribe_session_job, ctx.session_id)
    _set_job_meta(
        ctx.redis_client, job.id, phase="transcribing", progress_percent="oops"
    )

    app = _make_jdr_app(make_db_session_dep, ctx.redis_client)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/jobs/{job.id}",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert response.status_code == 200
    body = response.json()
    # phase is still a valid enum value, percent is dropped to null.
    assert body["phase"] == "transcribing"
    assert body["progress_percent"] is None


# ---------------------------------------------------------------------------
# US3 — a failed job keeps its last known progress (BD-10)
# ---------------------------------------------------------------------------


async def test_get_job_failed_preserves_last_progress(
    ctx, make_db_session_dep
):
    """A failed transcription exposes phase=failed and keeps the last known
    progress percent instead of resetting it to 0/null."""
    queue = get_default_queue(ctx.redis_client)
    job = enqueue_job(queue, transcribe_session_job, ctx.session_id)
    # Worker emitted some progress, then failed (phase flipped, percent kept).
    _set_job_meta(
        ctx.redis_client, job.id, phase="failed", progress_percent=73
    )

    app = _make_jdr_app(make_db_session_dep, ctx.redis_client)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/jobs/{job.id}",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["phase"] == "failed"
    assert body["progress_percent"] == 73


# ---------------------------------------------------------------------------
# Not found / forbidden cases
# ---------------------------------------------------------------------------


async def test_get_failed_summary_job_exposes_failure_reason(
    ctx, make_db_session_dep
):
    queue = get_default_queue(ctx.redis_client)
    job = enqueue_job(queue, generate_summary_job, ctx.session_id)
    _mark_job_failed(
        ctx.redis_client,
        job.id,
        "Traceback\napp.jobs.TransientJobError: APIConnectionError: Connection error.",
    )

    app = _make_jdr_app(make_db_session_dep, ctx.redis_client)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/jobs/{job.id}",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "summary"
    assert body["status"] == "failed"
    assert body["failure_reason"] == (
        "app.jobs.TransientJobError: APIConnectionError: Connection error."
    )


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

        campaign = await db.scalar(select(Campaign).limit(1))
        assert campaign is not None
        pj = Pj(
            name="Aragorn",
            owner_gm_key_id=ctx.gm_key_id,
            campaign_id=campaign.id,
        )
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
