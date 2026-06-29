"""BD-15 - DELETE /services/jdr/sessions/{session_id}.

The tests keep deletion behavior close to the public route contract because
the frontend relies on this endpoint to avoid destructive local mocks.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import fakeredis
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis import Redis
from rq.job import Job as RQJob, JobStatus as RQJobStatus
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.models import Profile, User, UserStatus
from app.core.redis_client import get_redis
from app.core.users import hash_password
from app.jobs import enqueue_job, get_default_queue
from app.jobs.jdr import generate_narrative_job
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    Artifact,
    AudioSource,
    Campaign,
    Chunk,
    Job,
    JobKind,
    JobStatus,
    Pj,
    Role,
    Session,
    SessionPjMapping,
    SessionPlayer,
    SessionState,
    Transcription,
)
from app.services.jdr.router import router as jdr_router


@dataclass
class DeleteSessionContext:
    plain_token: str
    gm_key: ApiKey
    campaign: Campaign


@pytest_asyncio.fixture
async def delete_ctx(db_session: AsyncSession) -> DeleteSessionContext:
    plain = f"gm-delete-session-{uuid4().hex}"
    api_key = ApiKey(
        name=f"gm-delete-session-{uuid4().hex[:8]}",
        hash=PasswordHasher().hash(plain),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
    )
    db_session.add(api_key)
    await db_session.flush()

    user = User(
        username=f"gm-delete-session-{uuid4().hex[:8]}",
        profile=Profile.GM,
        password_hash=hash_password("gm-password"),
        status=UserStatus.ACTIVE,
        api_key_id=api_key.id,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(user)
    await db_session.flush()

    campaign = Campaign(name="Delete session campaign", owner_user_id=user.id)
    db_session.add(campaign)
    await db_session.commit()
    await db_session.refresh(api_key)
    await db_session.refresh(campaign)
    api_key.test_campaign_id = campaign.id
    return DeleteSessionContext(
        plain_token=plain,
        gm_key=api_key,
        campaign=campaign,
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
    ctx: DeleteSessionContext,
    *,
    title: str = "Session to delete",
    state: SessionState = SessionState.CREATED,
) -> Session:
    session = Session(
        title=title,
        recorded_at=datetime.now(UTC),
        gm_key_id=ctx.gm_key.id,
        campaign_id=ctx.campaign.id,
        state=state,
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)
    return session


async def _create_gm_context(
    db_session: AsyncSession,
    *,
    token_prefix: str,
    campaign_name: str,
) -> DeleteSessionContext:
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
    return DeleteSessionContext(plain_token=plain, gm_key=api_key, campaign=campaign)


async def _create_player_token(
    db_session: AsyncSession, ctx: DeleteSessionContext
) -> str:
    pj = Pj(
        name=f"Player PJ {uuid4().hex[:6]}",
        owner_gm_key_id=ctx.gm_key.id,
        campaign_id=ctx.campaign.id,
    )
    db_session.add(pj)
    await db_session.flush()
    plain = f"player-delete-session-{uuid4().hex}"
    player_key = ApiKey(
        name=f"player-delete-session-{uuid4().hex[:8]}",
        hash=PasswordHasher().hash(plain),
        role=Role.PLAYER,
        status=ApiKeyStatus.ACTIVE,
        pj_id=pj.id,
    )
    db_session.add(player_key)
    await db_session.commit()
    return plain


def _set_rq_job_status(
    redis_client: fakeredis.FakeStrictRedis,
    job_id: str,
    status: RQJobStatus,
) -> None:
    job = RQJob.fetch(job_id, connection=redis_client)
    job.set_status(status)
    if status == RQJobStatus.STARTED:
        job.started_at = datetime.now(UTC)
    if status == RQJobStatus.FINISHED:
        job.ended_at = datetime.now(UTC)
    job.save()


async def _attach_current_rq_job(
    db_session: AsyncSession,
    session: Session,
    *,
    redis_client: fakeredis.FakeStrictRedis,
    rq_status: RQJobStatus = RQJobStatus.QUEUED,
    sql_status: JobStatus = JobStatus.QUEUED,
) -> str:
    queue = get_default_queue(redis_client)
    rq_job = enqueue_job(queue, generate_narrative_job, session.id)
    if rq_status != RQJobStatus.QUEUED:
        _set_rq_job_status(redis_client, rq_job.id, rq_status)

    job = Job(
        id=rq_job.id,
        kind=JobKind.NARRATIVE,
        session_id=session.id,
        status=sql_status,
        queued_at=datetime.now(UTC),
    )
    db_session.add(job)
    await db_session.flush()
    session.current_job_id = job.id
    await db_session.commit()
    return rq_job.id


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _session_count(db_session: AsyncSession, campaign_id) -> int:
    count = await db_session.scalar(
        select(func.count(Session.id)).where(Session.campaign_id == campaign_id)
    )
    assert count is not None
    return count


async def _seed_session_dependencies(
    db_session: AsyncSession,
    session: Session,
    *,
    audio_file: Path,
) -> tuple[Pj, Job]:
    pj = Pj(
        name=f"PJ {uuid4().hex[:6]}",
        owner_gm_key_id=session.gm_key_id,
        campaign_id=session.campaign_id,
    )
    db_session.add(pj)
    await db_session.flush()

    db_session.add_all(
        [
            AudioSource(
                session_id=session.id,
                path=f"audios/{session.id}.m4a",
                sha256="a" * 64,
                size_bytes=14,
                duration_seconds=10,
            ),
            Transcription(
                session_id=session.id,
                segments_json=[
                    {
                        "speaker": "speaker_1",
                        "start": 0,
                        "end": 1,
                        "text": "Bonjour",
                    }
                ],
                language="fr",
                model_used="test-model",
                provider="mock",
            ),
            Chunk(session_id=session.id, ordre=0, text="Bonjour", summary_text="Resume"),
            SessionPjMapping(
                session_id=session.id,
                speaker_label="speaker_1",
                pj_id=pj.id,
            ),
            SessionPlayer(session_id=session.id, pj_id=pj.id),
            Artifact(
                session_id=session.id,
                kind="summary",
                content_json={"markdown": "Resume"},
                model_used="test-model",
            ),
        ]
    )
    job = Job(
        id=f"job-{session.id.hex[:24]}",
        kind=JobKind.SUMMARY,
        session_id=session.id,
        status=JobStatus.SUCCEEDED,
        queued_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
    )
    db_session.add(job)
    session.current_job_id = job.id
    session.edited_transcript_md = "# Edited"
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    audio_file.write_bytes(b"fake-m4a-bytes")
    await db_session.commit()
    await db_session.refresh(pj)
    await db_session.refresh(job)
    return pj, job


async def test_delete_owned_session_returns_204_and_later_get_is_404(
    delete_ctx, db_session, make_db_session_dep
):
    session = await _create_session(db_session, delete_ctx)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        deleted = await client.delete(
            f"/services/jdr/sessions/{session.id}",
            headers=_auth_headers(delete_ctx.plain_token),
        )
        fetched = await client.get(
            f"/services/jdr/sessions/{session.id}",
            headers=_auth_headers(delete_ctx.plain_token),
        )

    assert deleted.status_code == 204
    assert deleted.content == b""
    assert fetched.status_code == 404
    assert fetched.json()["type"].endswith("/session-not-found")


async def test_delete_owned_session_removes_it_from_session_list(
    delete_ctx, db_session, make_db_session_dep
):
    deleted_session = await _create_session(db_session, delete_ctx, title="Delete me")
    kept_session = await _create_session(db_session, delete_ctx, title="Keep me")
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        deleted = await client.delete(
            f"/services/jdr/sessions/{deleted_session.id}",
            headers=_auth_headers(delete_ctx.plain_token),
        )
        listed = await client.get(
            f"/services/jdr/sessions?campaign_id={delete_ctx.campaign.id}",
            headers=_auth_headers(delete_ctx.plain_token),
        )

    assert deleted.status_code == 204
    assert listed.status_code == 200
    ids = {item["id"] for item in listed.json()["items"]}
    assert str(deleted_session.id) not in ids
    assert str(kept_session.id) in ids


async def test_delete_owned_session_decrements_campaign_session_count(
    delete_ctx, db_session, make_db_session_dep
):
    campaign_id = delete_ctx.campaign.id
    session = await _create_session(db_session, delete_ctx)
    await _create_session(db_session, delete_ctx, title="Another session")
    assert await _session_count(db_session, campaign_id) == 2

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        deleted = await client.delete(
            f"/services/jdr/sessions/{session.id}",
            headers=_auth_headers(delete_ctx.plain_token),
        )

    assert deleted.status_code == 204
    db_session.expire_all()
    assert await _session_count(db_session, campaign_id) == 1


async def test_delete_session_cascades_dependencies_and_audio_file(
    delete_ctx, db_session, make_db_session_dep, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    session = await _create_session(
        db_session, delete_ctx, state=SessionState.TRANSCRIBED
    )
    audio_file = tmp_path / "audios" / f"{session.id}.m4a"
    pj, job = await _seed_session_dependencies(
        db_session, session, audio_file=audio_file
    )
    session_id = session.id
    pj_id = pj.id
    job_id = job.id

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        deleted = await client.delete(
            f"/services/jdr/sessions/{session.id}",
            headers=_auth_headers(delete_ctx.plain_token),
        )

    assert deleted.status_code == 204
    assert not audio_file.exists()
    db_session.expire_all()
    assert await db_session.get(Session, session_id) is None
    assert await db_session.get(AudioSource, session_id) is None
    assert await db_session.get(Transcription, session_id) is None
    assert await db_session.get(Job, job_id) is None
    assert await db_session.get(Pj, pj_id) is not None
    assert not list(
        (
            await db_session.scalars(
                select(Chunk).where(Chunk.session_id == session_id)
            )
        ).all()
    )
    assert not list(
        (
            await db_session.scalars(
                select(Artifact).where(Artifact.session_id == session_id)
            )
        ).all()
    )
    assert not list(
        (
            await db_session.scalars(
                select(SessionPjMapping).where(
                    SessionPjMapping.session_id == session_id
                )
            )
        ).all()
    )
    assert not list(
        (
            await db_session.scalars(
                select(SessionPlayer).where(SessionPlayer.session_id == session_id)
            )
        ).all()
    )


async def test_delete_session_tolerates_missing_audio_file(
    delete_ctx, db_session, make_db_session_dep, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    session = await _create_session(
        db_session, delete_ctx, state=SessionState.TRANSCRIBED
    )
    audio_file = tmp_path / "audios" / f"{session.id}.m4a"
    await _seed_session_dependencies(db_session, session, audio_file=audio_file)
    session_id = session.id
    audio_file.unlink()

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        deleted = await client.delete(
            f"/services/jdr/sessions/{session.id}",
            headers=_auth_headers(delete_ctx.plain_token),
        )

    assert deleted.status_code == 204
    db_session.expire_all()
    assert await db_session.get(Session, session_id) is None


async def test_delete_session_keeps_pj_rows_but_removes_session_links(
    delete_ctx, db_session, make_db_session_dep, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    session = await _create_session(
        db_session, delete_ctx, state=SessionState.TRANSCRIBED
    )
    audio_file = tmp_path / "audios" / f"{session.id}.m4a"
    pj, _job = await _seed_session_dependencies(
        db_session, session, audio_file=audio_file
    )
    session_id = session.id
    pj_id = pj.id

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        deleted = await client.delete(
            f"/services/jdr/sessions/{session.id}",
            headers=_auth_headers(delete_ctx.plain_token),
        )

    assert deleted.status_code == 204
    db_session.expire_all()
    assert await db_session.get(Pj, pj_id) is not None
    assert await db_session.get(
        SessionPjMapping,
        {"session_id": session_id, "speaker_label": "speaker_1"},
    ) is None
    assert await db_session.get(
        SessionPlayer, {"session_id": session_id, "pj_id": pj_id}
    ) is None


async def test_delete_session_requires_authentication(
    delete_ctx, db_session, make_db_session_dep
):
    session = await _create_session(db_session, delete_ctx)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(f"/services/jdr/sessions/{session.id}")

    assert response.status_code == 401


async def test_delete_session_rejects_player_role(
    delete_ctx, db_session, make_db_session_dep
):
    session = await _create_session(db_session, delete_ctx)
    player_token = await _create_player_token(db_session, delete_ctx)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(
            f"/services/jdr/sessions/{session.id}",
            headers=_auth_headers(player_token),
        )

    assert response.status_code == 403


async def test_delete_session_cross_gm_returns_404(
    delete_ctx, db_session, make_db_session_dep
):
    session = await _create_session(db_session, delete_ctx)
    other_ctx = await _create_gm_context(
        db_session,
        token_prefix="other-delete-session",
        campaign_name="Other delete campaign",
    )
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(
            f"/services/jdr/sessions/{session.id}",
            headers=_auth_headers(other_ctx.plain_token),
        )

    assert response.status_code == 404
    assert response.json()["type"].endswith("/session-not-found")


async def test_delete_unknown_session_returns_404(delete_ctx, make_db_session_dep):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(
            f"/services/jdr/sessions/{uuid4()}",
            headers=_auth_headers(delete_ctx.plain_token),
        )

    assert response.status_code == 404
    assert response.json()["type"].endswith("/session-not-found")


async def test_delete_transcribing_session_succeeds(
    delete_ctx, db_session, make_db_session_dep
):
    # Story 7.1 / BD-21: a session can be deleted in ANY state, including
    # transcribing (previously a 409). With no active job there is nothing to
    # abort — the delete just proceeds.
    session = await _create_session(
        db_session, delete_ctx, state=SessionState.TRANSCRIBING
    )
    session_id = session.id
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(
            f"/services/jdr/sessions/{session_id}",
            headers=_auth_headers(delete_ctx.plain_token),
        )

    assert response.status_code == 204
    db_session.expire_all()
    assert await db_session.get(Session, session_id) is None


async def test_delete_session_with_active_current_job_aborts_and_deletes(
    delete_ctx, db_session, make_db_session_dep
):
    # Story 7.1 / BD-21: an active (queued) job no longer blocks deletion — it is
    # cancelled, then the session is deleted.
    session = await _create_session(
        db_session, delete_ctx, state=SessionState.TRANSCRIBED
    )
    redis_client = fakeredis.FakeStrictRedis()
    rq_job_id = await _attach_current_rq_job(
        db_session,
        session,
        redis_client=redis_client,
        rq_status=RQJobStatus.QUEUED,
    )
    session_id = session.id
    app = _make_jdr_app(make_db_session_dep, redis_client)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(
            f"/services/jdr/sessions/{session_id}",
            headers=_auth_headers(delete_ctx.plain_token),
        )

    assert response.status_code == 204
    db_session.expire_all()
    assert await db_session.get(Session, session_id) is None
    # The queued RQ job was cancelled so it can never start against a gone session.
    assert (
        RQJob.fetch(rq_job_id, connection=redis_client).get_status()
        == RQJobStatus.CANCELED
    )


async def test_delete_session_with_started_job_aborts_and_deletes(
    delete_ctx, db_session, make_db_session_dep
):
    # Story 7.1 / BD-21: a *running* job is stopped (best-effort) and the session
    # is deleted regardless. The stop command is fire-and-forget; the guarantee
    # under test is that deletion always succeeds.
    session = await _create_session(
        db_session, delete_ctx, state=SessionState.TRANSCRIBING
    )
    redis_client = fakeredis.FakeStrictRedis()
    await _attach_current_rq_job(
        db_session,
        session,
        redis_client=redis_client,
        rq_status=RQJobStatus.STARTED,
        sql_status=JobStatus.RUNNING,
    )
    session_id = session.id
    app = _make_jdr_app(make_db_session_dep, redis_client)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(
            f"/services/jdr/sessions/{session_id}",
            headers=_auth_headers(delete_ctx.plain_token),
        )

    assert response.status_code == 204
    db_session.expire_all()
    assert await db_session.get(Session, session_id) is None


async def test_delete_after_artifact_generation_enqueue_aborts_and_deletes(
    delete_ctx, db_session, make_db_session_dep
):
    # Story 7.1 / BD-21: enqueuing an artifact then deleting now succeeds — the
    # in-flight job is aborted and the Job row cascades away with the session.
    session = await _create_session(
        db_session, delete_ctx, state=SessionState.TRANSCRIBED
    )
    session_id = session.id
    redis_client = fakeredis.FakeStrictRedis()
    app = _make_jdr_app(make_db_session_dep, redis_client)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        queued = await client.post(
            f"/services/jdr/sessions/{session.id}/artifacts/narrative",
            headers=_auth_headers(delete_ctx.plain_token),
        )
        deleted = await client.delete(
            f"/services/jdr/sessions/{session.id}",
            headers=_auth_headers(delete_ctx.plain_token),
        )

    assert queued.status_code == 202
    assert deleted.status_code == 204
    job_id = queued.json()["id"]
    db_session.expire_all()
    assert await db_session.get(Session, session_id) is None
    # Job row cascades on session delete (jdr_jobs.session_id ON DELETE CASCADE).
    assert await db_session.get(Job, job_id) is None
    assert (
        RQJob.fetch(job_id, connection=redis_client).get_status()
        == RQJobStatus.CANCELED
    )


async def test_delete_allows_finished_rq_job_with_stale_sql_status(
    delete_ctx, db_session, make_db_session_dep
):
    session = await _create_session(
        db_session, delete_ctx, state=SessionState.TRANSCRIBED
    )
    redis_client = fakeredis.FakeStrictRedis()
    await _attach_current_rq_job(
        db_session,
        session,
        redis_client=redis_client,
        rq_status=RQJobStatus.FINISHED,
        sql_status=JobStatus.QUEUED,
    )
    session_id = session.id
    app = _make_jdr_app(make_db_session_dep, redis_client)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(
            f"/services/jdr/sessions/{session_id}",
            headers=_auth_headers(delete_ctx.plain_token),
        )

    assert response.status_code == 204
    db_session.expire_all()
    assert await db_session.get(Session, session_id) is None
