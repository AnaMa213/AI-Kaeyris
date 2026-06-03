"""BD-8 - GET /sessions/{id}/audio.

Serves stored source audio to the browser player, including byte ranges
for seek/scrub support.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import fakeredis
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.models import Profile
from app.core.redis_client import get_redis
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    AudioSource,
    CampaignRole,
    Role,
    Session,
    SessionState,
)
from app.services.jdr.router import router as jdr_router
from tests.services.jdr.campaign_fixtures import (
    make_campaign,
    make_membership,
    make_session,
    make_user,
    make_web_session,
)


def _make_jdr_app(make_db_session_dep: Callable[..., object]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(jdr_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = lambda: fakeredis.FakeStrictRedis()
    return app


async def _seed_session_with_audio(
    db: AsyncSession,
    data_dir: Path,
    *,
    plain_token: str = "gm-audio-get-token",
    state: SessionState = SessionState.TRANSCRIBED,
    audio_bytes: bytes = b"0123456789abcdef",
    purged: bool = False,
    write_file: bool = True,
) -> tuple[str, UUID, Path, bytes]:
    gm = ApiKey(
        name=f"gm-audio-get-{uuid4().hex[:8]}",
        hash=PasswordHasher().hash(plain_token),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
    )
    db.add(gm)
    await db.flush()

    session_id = uuid4()
    db.add(
        Session(
            id=session_id,
            title="Audio get test",
            recorded_at=datetime.now(UTC),
            gm_key_id=gm.id,
            state=state,
        )
    )
    audio_path_rel = f"audios/{session_id}.m4a"
    db.add(
        AudioSource(
            session_id=session_id,
            path=audio_path_rel,
            sha256="a" * 64,
            size_bytes=len(audio_bytes),
            duration_seconds=10,
            purged_at=datetime.now(UTC) if purged else None,
        )
    )
    await db.commit()

    audio_file = data_dir / audio_path_rel
    if write_file:
        audio_file.parent.mkdir(parents=True, exist_ok=True)
        audio_file.write_bytes(audio_bytes)

    return plain_token, session_id, audio_file, audio_bytes


async def test_get_audio_returns_full_file_with_player_headers(
    tmp_path: Path, db_session: AsyncSession, make_db_session_dep, monkeypatch
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, session_id, _audio_file, audio_bytes = await _seed_session_with_audio(
        db_session, tmp_path
    )
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{session_id}/audio",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 200
    assert response.content == audio_bytes
    assert response.headers["content-type"].startswith("audio/mp4")
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-length"] == str(len(audio_bytes))
    assert "private" in response.headers["cache-control"]


async def test_get_audio_allows_web_campaign_member(
    tmp_path: Path, db_session: AsyncSession, make_db_session_dep, monkeypatch
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    gm = await make_user(db_session, username="gm-audio-web", profile=Profile.GM)
    player = await make_user(
        db_session,
        username="player-audio-web",
        profile=Profile.USER,
    )
    campaign = await make_campaign(db_session, owner=gm, name="Audio web")
    await make_membership(db_session, user=gm, campaign=campaign, role=CampaignRole.GM)
    await make_membership(
        db_session,
        user=player,
        campaign=campaign,
        role=CampaignRole.PJ,
    )
    session = await make_session(db_session, owner=gm, campaign=campaign)
    session.state = SessionState.TRANSCRIBED
    audio_bytes = b"web-member-audio"
    audio_path_rel = f"audios/{session.id}.m4a"
    db_session.add(
        AudioSource(
            session_id=session.id,
            path=audio_path_rel,
            sha256="b" * 64,
            size_bytes=len(audio_bytes),
            duration_seconds=10,
        )
    )
    token = await make_web_session(db_session, user=player)
    audio_file = tmp_path / audio_path_rel
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    audio_file.write_bytes(audio_bytes)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("session", token)
        response = await client.get(f"/services/jdr/sessions/{session.id}/audio")

    assert response.status_code == 200
    assert response.content == audio_bytes


async def test_get_audio_supports_range_requests(
    tmp_path: Path, db_session: AsyncSession, make_db_session_dep, monkeypatch
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, session_id, _audio_file, audio_bytes = await _seed_session_with_audio(
        db_session, tmp_path
    )
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{session_id}/audio",
            headers={
                "Authorization": f"Bearer {plain}",
                "Range": "bytes=5-9",
            },
        )

    assert response.status_code == 206
    assert response.content == audio_bytes[5:10]
    assert response.headers["content-range"] == f"bytes 5-9/{len(audio_bytes)}"
    assert response.headers["content-length"] == "5"
    assert response.headers["accept-ranges"] == "bytes"


async def test_get_audio_returns_416_for_invalid_range(
    tmp_path: Path, db_session: AsyncSession, make_db_session_dep, monkeypatch
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, session_id, _audio_file, _audio_bytes = await _seed_session_with_audio(
        db_session, tmp_path
    )
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{session_id}/audio",
            headers={
                "Authorization": f"Bearer {plain}",
                "Range": "bytes=999-1000",
            },
        )

    assert response.status_code == 416
    assert response.headers["content-type"] == "application/problem+json"


async def test_get_audio_returns_404_when_audio_is_purged(
    tmp_path: Path, db_session: AsyncSession, make_db_session_dep, monkeypatch
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, session_id, _audio_file, _audio_bytes = await _seed_session_with_audio(
        db_session, tmp_path, purged=True
    )
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{session_id}/audio",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 404
    assert response.json()["type"].endswith("/audio-not-found")


async def test_get_audio_returns_404_when_file_is_missing(
    tmp_path: Path, db_session: AsyncSession, make_db_session_dep, monkeypatch
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    plain, session_id, _audio_file, _audio_bytes = await _seed_session_with_audio(
        db_session, tmp_path, write_file=False
    )
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{session_id}/audio",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 404
    assert response.json()["type"].endswith("/audio-not-found")


async def test_get_audio_cross_tenant_returns_404(
    tmp_path: Path, db_session: AsyncSession, make_db_session_dep, monkeypatch
):
    monkeypatch.setattr(
        "app.services.jdr.logic.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    _plain_a, session_id, audio_file, _audio_bytes = await _seed_session_with_audio(
        db_session, tmp_path, plain_token="gm-audio-a"
    )
    plain_b = "gm-audio-b"
    db_session.add(
        ApiKey(
            name="gm-audio-b",
            hash=PasswordHasher().hash(plain_b),
            role=Role.GM,
            status=ApiKeyStatus.ACTIVE,
        )
    )
    await db_session.commit()
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{session_id}/audio",
            headers={"Authorization": f"Bearer {plain_b}"},
        )

    assert response.status_code == 404
    assert response.json()["type"].endswith("/session-not-found")
    assert audio_file.exists()
