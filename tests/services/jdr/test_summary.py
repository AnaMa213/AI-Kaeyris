"""US2 / feature 002 — Routes `/artifacts/summary` (POST, GET, GET .md).

POST enqueue le job map-reduce (202). GET lit l'artefact persisté.
GET .md sert du Markdown avec l'en-tête de session standard.
Cross-mode isolation : refus 409 sur sessions `diarised`.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import fakeredis
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.redis_client import get_redis
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    Artifact,
    Chunk,
    Role,
    Session,
    SessionState,
    TranscriptionMode,
)
from app.services.jdr.router import router as jdr_router


def _make_jdr_app(make_db_session_dep: Callable[..., Any]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(jdr_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = lambda: fakeredis.FakeStrictRedis()
    return app


async def _seed_gm(db_session, plain_token: str) -> ApiKey:
    gm = ApiKey(
        name=f"gm-{uuid4().hex[:8]}",
        hash=PasswordHasher().hash(plain_token),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
    )
    db_session.add(gm)
    await db_session.commit()
    await db_session.refresh(gm)
    return gm


async def _seed_session_with_chunks(
    db_session,
    *,
    gm_id,
    mode=TranscriptionMode.NON_DIARISED,
    state=SessionState.TRANSCRIBED,
    chunk_count=2,
):
    session = Session(
        title="Summary route test",
        recorded_at=datetime.now(UTC),
        gm_key_id=gm_id,
        state=state,
        transcription_mode=mode,
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)
    if chunk_count and mode is TranscriptionMode.NON_DIARISED:
        for i in range(chunk_count):
            db_session.add(
                Chunk(session_id=session.id, ordre=i, text=f"chunk {i}")
            )
        await db_session.commit()
    return session


# ---------------------------------------------------------------------------
# POST /artifacts/summary
# ---------------------------------------------------------------------------


async def test_post_summary_returns_202_with_job_id(
    db_session, make_db_session_dep
):
    plain = "gm-summary-post"
    gm = await _seed_gm(db_session, plain)
    session = await _seed_session_with_chunks(db_session, gm_id=gm.id)

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/services/jdr/sessions/{session.id}/artifacts/summary",
            headers={"Authorization": f"Bearer {plain}"},
        )
    assert response.status_code == 202
    body = response.json()
    assert body["session_id"] == str(session.id)
    assert body["kind"] == "summary"
    assert body["status"] == "queued"
    assert "id" in body


async def test_post_summary_409_on_diarised(
    db_session, make_db_session_dep
):
    plain = "gm-summary-diarised"
    gm = await _seed_gm(db_session, plain)
    session = await _seed_session_with_chunks(
        db_session, gm_id=gm.id, mode=TranscriptionMode.DIARISED, chunk_count=0
    )

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/services/jdr/sessions/{session.id}/artifacts/summary",
            headers={"Authorization": f"Bearer {plain}"},
        )
    assert response.status_code == 409
    assert response.json()["type"].endswith("/wrong-mode")


async def test_post_summary_409_when_not_transcribed(
    db_session, make_db_session_dep
):
    plain = "gm-summary-not-transcribed"
    gm = await _seed_gm(db_session, plain)
    session = await _seed_session_with_chunks(
        db_session,
        gm_id=gm.id,
        state=SessionState.AUDIO_UPLOADED,
        chunk_count=0,
    )

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/services/jdr/sessions/{session.id}/artifacts/summary",
            headers={"Authorization": f"Bearer {plain}"},
        )
    assert response.status_code == 409
    assert response.json()["type"].endswith("/session-not-transcribed")


async def test_post_summary_409_when_no_chunks(
    db_session, make_db_session_dep
):
    """Session non_diarised transcribed mais sans chunks (cas dégénéré)."""
    plain = "gm-summary-no-chunks"
    gm = await _seed_gm(db_session, plain)
    session = await _seed_session_with_chunks(
        db_session, gm_id=gm.id, chunk_count=0
    )

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/services/jdr/sessions/{session.id}/artifacts/summary",
            headers={"Authorization": f"Bearer {plain}"},
        )
    assert response.status_code == 409
    assert response.json()["type"].endswith("/no-chunks")


# ---------------------------------------------------------------------------
# GET /artifacts/summary
# ---------------------------------------------------------------------------


async def test_get_summary_404_when_not_generated(
    db_session, make_db_session_dep
):
    plain = "gm-summary-not-ready"
    gm = await _seed_gm(db_session, plain)
    session = await _seed_session_with_chunks(db_session, gm_id=gm.id)

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{session.id}/artifacts/summary",
            headers={"Authorization": f"Bearer {plain}"},
        )
    assert response.status_code == 404
    assert response.json()["type"].endswith("/artifact-not-ready")


async def test_get_summary_returns_artifact_json(
    db_session, make_db_session_dep
):
    plain = "gm-summary-get"
    gm = await _seed_gm(db_session, plain)
    session = await _seed_session_with_chunks(db_session, gm_id=gm.id)
    db_session.add(
        Artifact(
            session_id=session.id,
            kind="summary",
            content_json={"text": "Résumé global de test."},
            model_used="test-model",
        )
    )
    await db_session.commit()

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{session.id}/artifacts/summary",
            headers={"Authorization": f"Bearer {plain}"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == str(session.id)
    assert body["text"] == "Résumé global de test."
    assert body["model_used"] == "test-model"
    assert "generated_at" in body


async def test_get_summary_409_on_diarised(
    db_session, make_db_session_dep
):
    plain = "gm-summary-get-wrong"
    gm = await _seed_gm(db_session, plain)
    session = await _seed_session_with_chunks(
        db_session, gm_id=gm.id, mode=TranscriptionMode.DIARISED, chunk_count=0
    )

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{session.id}/artifacts/summary",
            headers={"Authorization": f"Bearer {plain}"},
        )
    assert response.status_code == 409
    assert response.json()["type"].endswith("/wrong-mode")


# ---------------------------------------------------------------------------
# GET /artifacts/summary.md
# ---------------------------------------------------------------------------


async def test_get_summary_md_returns_markdown(
    db_session, make_db_session_dep
):
    plain = "gm-summary-md"
    gm = await _seed_gm(db_session, plain)
    session = await _seed_session_with_chunks(db_session, gm_id=gm.id)
    db_session.add(
        Artifact(
            session_id=session.id,
            kind="summary",
            content_json={"text": "Le récit complet."},
            model_used="test-model",
        )
    )
    await db_session.commit()

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{session.id}/artifacts/summary.md",
            headers={"Authorization": f"Bearer {plain}"},
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    body = response.text
    assert "Le récit complet" in body
    # En-tête session standard (cf. render_session_header)
    assert "# Session" in body or "## " in body
