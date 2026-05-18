"""Cross-mode isolation tests (feature 002 / FR-014 non-régression).

Le mode d'une session contraint quels endpoints sont utilisables :
- `diarised` : /transcription, /mapping (Jalon 5) — pas /chunks, pas /summary, pas /players
- `non_diarised` : /chunks, /summary, /players — pas /transcription, pas /mapping

Ce fichier sera complété par US3 (T039) avec les checks /mapping et /summary.
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
    Role,
    Session,
    SessionState,
    Transcription,
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


async def _seed_gm_with_session(
    db_session,
    plain: str,
    *,
    mode: TranscriptionMode,
):
    gm = ApiKey(
        name=f"gm-{uuid4().hex[:8]}",
        hash=PasswordHasher().hash(plain),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
    )
    db_session.add(gm)
    await db_session.commit()
    await db_session.refresh(gm)

    session = Session(
        title="Iso test",
        recorded_at=datetime.now(UTC),
        gm_key_id=gm.id,
        state=SessionState.TRANSCRIBED,
        transcription_mode=mode,
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)
    return gm, session


# ---------------------------------------------------------------------------
# /transcription et /transcription.md : disponibles SEULEMENT en diarised
# ---------------------------------------------------------------------------


async def test_get_transcription_409_on_non_diarised(
    db_session, make_db_session_dep
):
    plain = "gm-iso-transcription"
    _, session = await _seed_gm_with_session(
        db_session, plain, mode=TranscriptionMode.NON_DIARISED
    )

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{session.id}/transcription",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 409
    assert response.json()["type"].endswith("/wrong-mode")


async def test_get_transcription_md_409_on_non_diarised(
    db_session, make_db_session_dep
):
    plain = "gm-iso-transcription-md"
    _, session = await _seed_gm_with_session(
        db_session, plain, mode=TranscriptionMode.NON_DIARISED
    )

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{session.id}/transcription.md",
            headers={"Authorization": f"Bearer {plain}"},
        )
    assert response.status_code == 409
    assert response.json()["type"].endswith("/wrong-mode")


async def test_get_transcription_still_works_on_diarised(
    db_session, make_db_session_dep
):
    """Non-régression FR-014 : /transcription reste OK sur diarised."""
    plain = "gm-iso-diarised-ok"
    gm, session = await _seed_gm_with_session(
        db_session, plain, mode=TranscriptionMode.DIARISED
    )
    # Seed une transcription pour qu'elle soit lisible
    db_session.add(
        Transcription(
            session_id=session.id,
            segments_json=[
                {
                    "speaker_label": "speaker_1",
                    "start_seconds": 0.0,
                    "end_seconds": 1.0,
                    "text": "Bonjour.",
                }
            ],
            language="fr",
            model_used="mock:whisper",
            provider="mock",
        )
    )
    await db_session.commit()

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{session.id}/transcription",
            headers={"Authorization": f"Bearer {plain}"},
        )
    assert response.status_code == 200
    body = response.json()
    assert len(body["segments"]) == 1
    _ = gm
