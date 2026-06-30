"""Epic 8 / US5 (BD-27) - player reads for summary and elements."""

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
    Pj,
    Role,
    Session,
    SessionPjMapping,
    SessionPlayer,
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


async def _seed_player_artifacts(db_session) -> dict[str, Any]:
    hasher = PasswordHasher()
    player_plain = "player-artifact-read"

    gm = ApiKey(
        name=f"gm-{uuid4().hex[:8]}",
        hash=hasher.hash("gm-artifact-read"),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
    )
    db_session.add(gm)
    await db_session.flush()

    campaign = Campaign(name="Player artifact reads", owner_user_id=uuid4())
    db_session.add(campaign)
    await db_session.flush()

    pj_player = Pj(name="Mira", owner_gm_key_id=gm.id, campaign_id=campaign.id)
    pj_other = Pj(name="Noam", owner_gm_key_id=gm.id, campaign_id=campaign.id)
    db_session.add_all([pj_player, pj_other])
    await db_session.flush()

    player = ApiKey(
        name=f"player-{uuid4().hex[:8]}",
        hash=hasher.hash(player_plain),
        role=Role.PLAYER,
        status=ApiKeyStatus.ACTIVE,
        pj_id=pj_player.id,
    )
    db_session.add(player)
    await db_session.flush()

    visible = Session(
        title="Visible session",
        recorded_at=datetime.now(UTC),
        gm_key_id=gm.id,
        campaign_id=campaign.id,
        state=SessionState.TRANSCRIBED,
    )
    hidden = Session(
        title="Hidden session",
        recorded_at=datetime.now(UTC),
        gm_key_id=gm.id,
        campaign_id=campaign.id,
        state=SessionState.TRANSCRIBED,
    )
    db_session.add_all([visible, hidden])
    await db_session.flush()

    db_session.add_all(
        [
            SessionPjMapping(
                session_id=visible.id,
                speaker_label="speaker_1",
                pj_id=pj_player.id,
            ),
            SessionPjMapping(
                session_id=hidden.id,
                speaker_label="speaker_1",
                pj_id=pj_other.id,
            ),
            Artifact(
                session_id=visible.id,
                kind="summary",
                content_json={"text": "Player-visible summary."},
                model_used="test",
                generated_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            ),
            Artifact(
                session_id=visible.id,
                kind="elements",
                content_json={
                    "elements": [
                        {
                            "category": "PNJ",
                            "name": "Archiviste",
                            "description": "Knows the old route.",
                        }
                    ]
                },
                model_used="test",
                generated_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            ),
            Artifact(
                session_id=hidden.id,
                kind="summary",
                content_json={"text": "Hidden summary."},
                model_used="test",
            ),
            Artifact(
                session_id=hidden.id,
                kind="elements",
                content_json={"elements": []},
                model_used="test",
            ),
        ]
    )
    await db_session.commit()

    return {
        "player_plain": player_plain,
        "visible_session_id": visible.id,
        "hidden_session_id": hidden.id,
    }


async def test_player_reads_summary_and_elements_for_mapped_session(
    db_session, make_db_session_dep
):
    fx = await _seed_player_artifacts(db_session)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {fx['player_plain']}"}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        summary = await client.get(
            f"/services/jdr/me/sessions/{fx['visible_session_id']}/summary",
            headers=headers,
        )
        summary_md = await client.get(
            f"/services/jdr/me/sessions/{fx['visible_session_id']}/summary.md",
            headers=headers,
        )
        elements = await client.get(
            f"/services/jdr/me/sessions/{fx['visible_session_id']}/elements",
            headers=headers,
        )
        elements_md = await client.get(
            f"/services/jdr/me/sessions/{fx['visible_session_id']}/elements.md",
            headers=headers,
        )

    assert summary.status_code == 200
    assert summary.json()["text"] == "Player-visible summary."
    assert summary.json()["is_edited"] is False
    assert summary_md.status_code == 200
    assert "Player-visible summary." in summary_md.text

    assert elements.status_code == 200
    assert elements.json()["elements"] == [
        {
            "category": "PNJ",
            "name": "Archiviste",
            "description": "Knows the old route.",
        }
    ]
    assert elements_md.status_code == 200
    assert "Archiviste" in elements_md.text


async def test_player_cannot_read_summary_or_elements_for_unmapped_session(
    db_session, make_db_session_dep
):
    fx = await _seed_player_artifacts(db_session)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {fx['player_plain']}"}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        summary = await client.get(
            f"/services/jdr/me/sessions/{fx['hidden_session_id']}/summary",
            headers=headers,
        )
        elements = await client.get(
            f"/services/jdr/me/sessions/{fx['hidden_session_id']}/elements",
            headers=headers,
        )

    assert summary.status_code == 403
    assert summary.json()["type"].endswith("/player-forbidden")
    assert elements.status_code == 403
    assert elements.json()["type"].endswith("/player-forbidden")


async def test_player_reads_summary_and_elements_for_non_diarised_player_list(
    db_session, make_db_session_dep
):
    hasher = PasswordHasher()
    player_plain = "player-non-diarised-artifacts"

    gm = ApiKey(
        name=f"gm-{uuid4().hex[:8]}",
        hash=hasher.hash("gm-non-diarised-artifacts"),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
    )
    db_session.add(gm)
    await db_session.flush()

    campaign = Campaign(name="Non diarised reads", owner_user_id=uuid4())
    db_session.add(campaign)
    await db_session.flush()

    pj_player = Pj(name="Mira", owner_gm_key_id=gm.id, campaign_id=campaign.id)
    pj_other = Pj(name="Noam", owner_gm_key_id=gm.id, campaign_id=campaign.id)
    db_session.add_all([pj_player, pj_other])
    await db_session.flush()

    player = ApiKey(
        name=f"player-{uuid4().hex[:8]}",
        hash=hasher.hash(player_plain),
        role=Role.PLAYER,
        status=ApiKeyStatus.ACTIVE,
        pj_id=pj_player.id,
    )
    db_session.add(player)
    await db_session.flush()

    visible = Session(
        title="Visible non diarised",
        recorded_at=datetime.now(UTC),
        gm_key_id=gm.id,
        campaign_id=campaign.id,
        state=SessionState.TRANSCRIBED,
        transcription_mode=TranscriptionMode.NON_DIARISED,
    )
    hidden = Session(
        title="Hidden non diarised",
        recorded_at=datetime.now(UTC),
        gm_key_id=gm.id,
        campaign_id=campaign.id,
        state=SessionState.TRANSCRIBED,
        transcription_mode=TranscriptionMode.NON_DIARISED,
    )
    db_session.add_all([visible, hidden])
    await db_session.flush()

    db_session.add_all(
        [
            SessionPlayer(session_id=visible.id, pj_id=pj_player.id),
            SessionPlayer(session_id=hidden.id, pj_id=pj_other.id),
            Artifact(
                session_id=visible.id,
                kind="summary",
                content_json={"text": "Visible non-diarised summary."},
                model_used="test",
                generated_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            ),
            Artifact(
                session_id=visible.id,
                kind="elements",
                content_json={
                    "elements": [
                        {
                            "category": "Clue",
                            "name": "Map",
                            "description": "Points to the vault.",
                        }
                    ]
                },
                model_used="test",
                generated_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            ),
            Artifact(
                session_id=hidden.id,
                kind="summary",
                content_json={"text": "Hidden non-diarised summary."},
                model_used="test",
            ),
            Artifact(
                session_id=hidden.id,
                kind="elements",
                content_json={"elements": []},
                model_used="test",
            ),
        ]
    )
    await db_session.commit()

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {player_plain}"}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        visible_summary = await client.get(
            f"/services/jdr/me/sessions/{visible.id}/summary",
            headers=headers,
        )
        visible_elements = await client.get(
            f"/services/jdr/me/sessions/{visible.id}/elements",
            headers=headers,
        )
        hidden_summary = await client.get(
            f"/services/jdr/me/sessions/{hidden.id}/summary",
            headers=headers,
        )
        hidden_elements = await client.get(
            f"/services/jdr/me/sessions/{hidden.id}/elements",
            headers=headers,
        )

    assert visible_summary.status_code == 200
    assert visible_summary.json()["text"] == "Visible non-diarised summary."
    assert visible_elements.status_code == 200
    assert visible_elements.json()["elements"][0]["name"] == "Map"
    assert hidden_summary.status_code == 403
    assert hidden_summary.json()["type"].endswith("/player-forbidden")
    assert hidden_elements.status_code == 403
    assert hidden_elements.json()["type"].endswith("/player-forbidden")
