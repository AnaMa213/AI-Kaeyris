"""US1 — Audio upload endpoint.

POST /services/jdr/sessions/{id}/audio accepts a multipart M4A, persists
it on disk under KAEYRIS_DATA_DIR/audios/<session_id>.m4a, records the
AudioSource row, transitions the session state to ``audio_uploaded``,
and enqueues a transcription job (RQ — its body is implemented in the
next sub-lot 3c).
"""

from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import fakeredis
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis import Redis
from sqlalchemy import select
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
    Job,
    JobKind,
    JobStatus,
    Role,
    Session,
    SessionState,
)
from app.services.jdr.router import router as jdr_router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seeded_gm(db_session: AsyncSession) -> tuple[str, ApiKey]:
    plain = "gm-upload-token"
    api_key = ApiKey(
        name="gm-upload-test",
        hash=PasswordHasher().hash(plain),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
        pj_id=None,
    )
    db_session.add(api_key)
    await db_session.flush()
    user = User(
        username="gm-upload-test",
        profile=Profile.GM,
        password_hash=hash_password("gm-password"),
        status=UserStatus.ACTIVE,
        api_key_id=api_key.id,
    )
    db_session.add(user)
    await db_session.flush()
    campaign = Campaign(name="Upload test", owner_user_id=user.id)
    db_session.add(campaign)
    await db_session.commit()
    await db_session.refresh(api_key)
    api_key.test_campaign_id = campaign.id
    return plain, api_key


def _make_jdr_app(
    make_db_session_dep: Callable[..., object],
    redis_client: Redis,
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(jdr_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = lambda: redis_client
    return app


async def _create_session(
    client: AsyncClient,
    token: str,
    campaign_id: object,
    title: str = "Upload test",
) -> str:
    resp = await client.post(
        "/services/jdr/sessions",
        json={
            "title": title,
            "recorded_at": "2026-05-04T20:30:00+00:00",
            "campaign_id": str(campaign_id),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_upload_m4a_returns_202_with_job_id(
    tmp_path: Path, seeded_gm, make_db_session_dep, monkeypatch
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, api_key = seeded_gm
    redis_client = fakeredis.FakeStrictRedis()
    app = _make_jdr_app(make_db_session_dep, redis_client)
    transport = ASGITransport(app=app)

    fake_audio = b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 2000

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        session_id = await _create_session(client, plain, api_key.test_campaign_id)
        response = await client.post(
            f"/services/jdr/sessions/{session_id}/audio",
            files={"audio": ("demo.m4a", fake_audio, "audio/mp4")},
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["session_id"] == session_id
    assert body["size_bytes"] == len(fake_audio)
    assert len(body["sha256"]) == 64
    assert "job_id" in body and isinstance(body["job_id"], str)
    # duration_seconds is best-effort (ffprobe may not be installed)
    assert "duration_seconds" in body


async def test_upload_writes_file_to_data_dir(
    tmp_path: Path, seeded_gm, make_db_session_dep, monkeypatch
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, api_key = seeded_gm
    app = _make_jdr_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)
    fake_audio = b"fake-m4a-content-for-test"

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        session_id = await _create_session(client, plain, api_key.test_campaign_id)
        await client.post(
            f"/services/jdr/sessions/{session_id}/audio",
            files={"audio": ("session.m4a", fake_audio, "audio/mp4")},
            headers={"Authorization": f"Bearer {plain}"},
        )

    expected = tmp_path / "audios" / f"{session_id}.m4a"
    assert expected.is_file()
    assert expected.read_bytes() == fake_audio


async def test_upload_transitions_session_state(
    tmp_path: Path,
    seeded_gm,
    make_db_session_dep,
    db_session: AsyncSession,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, api_key = seeded_gm
    app = _make_jdr_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        session_id = await _create_session(client, plain, api_key.test_campaign_id)
        await client.post(
            f"/services/jdr/sessions/{session_id}/audio",
            files={"audio": ("a.m4a", b"abc", "audio/mp4")},
            headers={"Authorization": f"Bearer {plain}"},
        )

    row = await db_session.scalar(
        select(Session).where(Session.id == UUID(session_id))
    )
    assert row is not None
    assert row.state == SessionState.AUDIO_UPLOADED


async def test_upload_creates_audio_source_row(
    tmp_path: Path,
    seeded_gm,
    make_db_session_dep,
    db_session: AsyncSession,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, api_key = seeded_gm
    app = _make_jdr_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)
    fake_audio = b"another-audio-blob"

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        session_id = await _create_session(client, plain, api_key.test_campaign_id)
        await client.post(
            f"/services/jdr/sessions/{session_id}/audio",
            files={"audio": ("ok.m4a", fake_audio, "audio/mp4")},
            headers={"Authorization": f"Bearer {plain}"},
        )

    audio = await db_session.scalar(
        select(AudioSource).where(AudioSource.session_id == UUID(session_id))
    )
    assert audio is not None
    assert audio.size_bytes == len(fake_audio)
    assert len(audio.sha256) == 64
    assert audio.purged_at is None
    assert audio.path.endswith(f"{session_id}.m4a")


async def test_upload_enqueues_transcription_job(
    tmp_path: Path, seeded_gm, make_db_session_dep, monkeypatch
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, api_key = seeded_gm
    redis_client = fakeredis.FakeStrictRedis()
    app = _make_jdr_app(make_db_session_dep, redis_client)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        session_id = await _create_session(client, plain, api_key.test_campaign_id)
        before_keys = set(redis_client.keys("rq:job:*"))
        await client.post(
            f"/services/jdr/sessions/{session_id}/audio",
            files={"audio": ("a.m4a", b"xy", "audio/mp4")},
            headers={"Authorization": f"Bearer {plain}"},
        )
        after_keys = set(redis_client.keys("rq:job:*"))

    # Exactly one new RQ job key landed in Redis.
    assert len(after_keys - before_keys) == 1


async def test_upload_sets_session_current_job_id(
    tmp_path: Path,
    seeded_gm,
    make_db_session_dep,
    db_session: AsyncSession,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, api_key = seeded_gm
    app = _make_jdr_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        session_id = await _create_session(client, plain, api_key.test_campaign_id)
        response = await client.post(
            f"/services/jdr/sessions/{session_id}/audio",
            files={"audio": ("a.m4a", b"xy", "audio/mp4")},
            headers={"Authorization": f"Bearer {plain}"},
        )
        detail = await client.get(
            f"/services/jdr/sessions/{session_id}",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 202
    job_id = response.json()["job_id"]
    row = await db_session.scalar(
        select(Session).where(Session.id == UUID(session_id))
    )
    assert row is not None
    assert row.current_job_id == job_id

    job_row = await db_session.get(Job, job_id)
    assert job_row is not None
    assert job_row.kind == JobKind.TRANSCRIPTION
    assert job_row.status == JobStatus.QUEUED
    assert detail.status_code == 200
    assert detail.json()["current_job_id"] == job_id


# ---------------------------------------------------------------------------
# Validation & error cases
# ---------------------------------------------------------------------------


async def test_upload_rejects_non_audio_mime(
    tmp_path: Path, seeded_gm, make_db_session_dep, monkeypatch
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, api_key = seeded_gm
    app = _make_jdr_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        session_id = await _create_session(client, plain, api_key.test_campaign_id)
        response = await client.post(
            f"/services/jdr/sessions/{session_id}/audio",
            files={"audio": ("wrong.txt", b"hello", "text/plain")},
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 415
    assert response.headers["content-type"] == "application/problem+json"


async def test_upload_double_returns_409(
    tmp_path: Path, seeded_gm, make_db_session_dep, monkeypatch
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, api_key = seeded_gm
    app = _make_jdr_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        session_id = await _create_session(client, plain, api_key.test_campaign_id)
        first = await client.post(
            f"/services/jdr/sessions/{session_id}/audio",
            files={"audio": ("a.m4a", b"first", "audio/mp4")},
            headers={"Authorization": f"Bearer {plain}"},
        )
        assert first.status_code == 202

        second = await client.post(
            f"/services/jdr/sessions/{session_id}/audio",
            files={"audio": ("b.m4a", b"second", "audio/mp4")},
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert second.status_code == 409


async def test_upload_on_unknown_session_returns_404(
    tmp_path: Path, seeded_gm, make_db_session_dep, monkeypatch
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, _ = seeded_gm
    app = _make_jdr_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)
    unknown_uuid = "00000000-0000-0000-0000-000000000000"

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/services/jdr/sessions/{unknown_uuid}/audio",
            files={"audio": ("a.m4a", b"xx", "audio/mp4")},
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 404


async def test_upload_requires_authentication(
    tmp_path: Path, make_db_session_dep, monkeypatch
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    app = _make_jdr_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)
    bogus_uuid = "11111111-1111-1111-1111-111111111111"

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/services/jdr/sessions/{bogus_uuid}/audio",
            files={"audio": ("a.m4a", b"xx", "audio/mp4")},
        )

    assert response.status_code == 401
