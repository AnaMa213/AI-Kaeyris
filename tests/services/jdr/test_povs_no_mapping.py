"""US3 / sub-lot 5b — POV refused when there's no mapping yet (FR-011).

POST /services/jdr/sessions/{id}/artifacts/povs returns 409 with a
clear message when the session has no speaker→PJ mapping configured.
The error must point the caller to the missing step (set the mapping
first via PUT /mapping).
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
)
from app.services.jdr.router import router as jdr_router


def _make_jdr_app(make_db_session_dep: Callable[..., Any]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(jdr_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = lambda: fakeredis.FakeStrictRedis()
    return app


async def _seed_transcribed_session_no_mapping(db_session, plain_token: str):
    gm = ApiKey(
        name=f"gm-{uuid4().hex[:8]}",
        hash=PasswordHasher().hash(plain_token),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
    )
    db_session.add(gm)
    await db_session.commit()
    await db_session.refresh(gm)

    session = Session(
        title="POV-no-mapping",
        recorded_at=datetime.now(UTC),
        gm_key_id=gm.id,
        state=SessionState.TRANSCRIBED,
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)

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
            completed_at=datetime.now(UTC),
        )
    )
    await db_session.commit()
    return gm, session


async def test_post_povs_returns_409_when_no_mapping(
    db_session, make_db_session_dep
):
    plain = "gm-pov-nomap"
    _, session = await _seed_transcribed_session_no_mapping(db_session, plain)

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/services/jdr/sessions/{session.id}/artifacts/povs",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 409
    body = response.json()
    # FR-011 — the error message must explicitly mention the missing
    # mapping so the operator knows what to do next.
    haystack = " ".join(
        str(body.get(k, "")) for k in ("detail", "title", "type")
    ).lower()
    assert "mapping" in haystack, f"Error body should mention 'mapping': {body}"
