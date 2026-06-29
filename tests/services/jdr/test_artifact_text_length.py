"""Epic 8 / US4 (BD-25) - long manual text edits stay intact."""

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
    Campaign,
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


async def test_patch_summary_accepts_long_text_without_schema_cap(
    db_session, make_db_session_dep
):
    plain = "gm-long-summary"
    gm = ApiKey(
        name=f"gm-{uuid4().hex[:8]}",
        hash=PasswordHasher().hash(plain),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
    )
    db_session.add(gm)
    await db_session.flush()

    campaign = Campaign(name="Long edit campaign", owner_user_id=uuid4())
    db_session.add(campaign)
    await db_session.flush()

    session = Session(
        title="Long summary edit",
        recorded_at=datetime.now(UTC),
        gm_key_id=gm.id,
        campaign_id=campaign.id,
        state=SessionState.TRANSCRIBED,
        transcription_mode=TranscriptionMode.NON_DIARISED,
    )
    db_session.add(session)
    await db_session.flush()

    db_session.add(
        Artifact(
            session_id=session.id,
            kind="summary",
            content_json={"text": "Initial generated summary."},
            model_used="test",
            generated_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        )
    )
    await db_session.commit()

    long_text = "\n".join(
        f"Scene {idx}: " + "details " * 30 for idx in range(1_000)
    )
    assert len(long_text) > 200_000

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        patch = await client.patch(
            f"/services/jdr/sessions/{session.id}/artifacts/summary",
            headers={"Authorization": f"Bearer {plain}"},
            json={"text": long_text},
        )
        get = await client.get(
            f"/services/jdr/sessions/{session.id}/artifacts/summary",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert patch.status_code == 200
    assert patch.json()["text"] == long_text
    assert get.status_code == 200
    assert get.json()["text"] == long_text
