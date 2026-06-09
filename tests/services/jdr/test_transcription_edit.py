"""BD-13 - persisted edited Markdown transcription."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import fakeredis
import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.redis_client import get_redis
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    Chunk,
    Pj,
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


async def _seed_gm(db, *, plain: str) -> ApiKey:
    gm = ApiKey(
        name=f"gm-edit-{uuid4().hex[:8]}",
        hash=PasswordHasher().hash(plain),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
    )
    db.add(gm)
    await db.flush()
    return gm


async def _seed_player(db, *, plain: str, gm_id: UUID) -> ApiKey:
    pj = Pj(
        name=f"PJ {uuid4().hex[:8]}",
        owner_gm_key_id=gm_id,
        campaign_id=uuid4(),
    )
    db.add(pj)
    await db.flush()
    player = ApiKey(
        name=f"player-edit-{uuid4().hex[:8]}",
        hash=PasswordHasher().hash(plain),
        role=Role.PLAYER,
        status=ApiKeyStatus.ACTIVE,
        pj_id=pj.id,
    )
    db.add(player)
    await db.flush()
    return player


async def _seed_session(
    db,
    *,
    gm_id: UUID,
    state: SessionState = SessionState.TRANSCRIBED,
    mode: TranscriptionMode = TranscriptionMode.DIARISED,
    edited: str | None = None,
) -> Session:
    session = Session(
        id=uuid4(),
        title=f"Session edit {uuid4().hex[:8]}",
        recorded_at=datetime.now(UTC),
        gm_key_id=gm_id,
        state=state,
        transcription_mode=mode,
        edited_transcript_md=edited,
    )
    db.add(session)
    await db.flush()
    if mode is TranscriptionMode.DIARISED:
        db.add(
            Transcription(
                session_id=session.id,
                segments_json=[
                    {
                        "speaker_label": "speaker_1",
                        "start_seconds": 0.0,
                        "end_seconds": 1.0,
                        "text": "Automatic diarised text",
                    }
                ],
                language="fr",
                model_used="test:whisper",
                provider="test",
            )
        )
    else:
        db.add(Chunk(session_id=session.id, ordre=0, text="Automatic chunk text"))
    await db.commit()
    return session


async def test_put_transcription_edit_returns_saved_content(
    db_session, make_db_session_dep
):
    plain = "gm-edit-save"
    gm = await _seed_gm(db_session, plain=plain)
    session = await _seed_session(db_session, gm_id=gm.id)
    app = _make_jdr_app(make_db_session_dep)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.put(
            f"/services/jdr/sessions/{session.id}/transcription",
            headers={"Authorization": f"Bearer {plain}"},
            json={"content_md": "## Scene\n\nCorrected text."},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == str(session.id)
    assert body["content_md"] == "## Scene\n\nCorrected text."
    assert body["is_edited"] is True
    assert "updated_at" in body


async def test_get_transcription_md_returns_edited_markdown_when_present(
    db_session, make_db_session_dep
):
    plain = "gm-edit-read"
    gm = await _seed_gm(db_session, plain=plain)
    session = await _seed_session(db_session, gm_id=gm.id)
    app = _make_jdr_app(make_db_session_dep)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.put(
            f"/services/jdr/sessions/{session.id}/transcription",
            headers={"Authorization": f"Bearer {plain}"},
            json={"content_md": "## Edited\n\nOnly corrected content."},
        )
        response = await client.get(
            f"/services/jdr/sessions/{session.id}/transcription.md",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.text == "## Edited\n\nOnly corrected content."


async def test_get_transcription_md_rejects_stale_edit_when_not_transcribed(
    db_session, make_db_session_dep
):
    plain = "gm-edit-stale"
    gm = await _seed_gm(db_session, plain=plain)
    session = await _seed_session(
        db_session,
        gm_id=gm.id,
        state=SessionState.AUDIO_UPLOADED,
        edited="Stale edit from an older audio.",
    )
    app = _make_jdr_app(make_db_session_dep)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/services/jdr/sessions/{session.id}/transcription.md",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 404
    assert response.json()["type"].endswith("/transcription-not-ready")


async def test_get_transcription_md_keeps_automatic_fallback_without_edit(
    db_session, make_db_session_dep
):
    plain = "gm-edit-fallback"
    gm = await _seed_gm(db_session, plain=plain)
    session = await _seed_session(db_session, gm_id=gm.id)
    app = _make_jdr_app(make_db_session_dep)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/services/jdr/sessions/{session.id}/transcription.md",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 200
    assert "Automatic diarised text" in response.text


async def test_put_transcription_edit_replaces_previous_content(
    db_session, make_db_session_dep
):
    plain = "gm-edit-replace"
    gm = await _seed_gm(db_session, plain=plain)
    session = await _seed_session(db_session, gm_id=gm.id)
    app = _make_jdr_app(make_db_session_dep)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.put(
            f"/services/jdr/sessions/{session.id}/transcription",
            headers={"Authorization": f"Bearer {plain}"},
            json={"content_md": "First edit"},
        )
        response = await client.put(
            f"/services/jdr/sessions/{session.id}/transcription",
            headers={"Authorization": f"Bearer {plain}"},
            json={"content_md": "Second edit"},
        )
        md = await client.get(
            f"/services/jdr/sessions/{session.id}/transcription.md",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 200
    assert response.json()["content_md"] == "Second edit"
    assert md.text == "Second edit"


async def test_openapi_exposes_transcription_edit_contract(make_db_session_dep):
    app = _make_jdr_app(make_db_session_dep)
    schema = app.openapi()

    operation = schema["paths"]["/services/jdr/sessions/{session_id}/transcription"][
        "put"
    ]
    request_ref = operation["requestBody"]["content"]["application/json"]["schema"][
        "$ref"
    ]
    response_ref = operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["$ref"]

    request_schema = schema["components"]["schemas"][request_ref.rsplit("/", 1)[-1]]
    response_schema = schema["components"]["schemas"][response_ref.rsplit("/", 1)[-1]]
    assert "content_md" in request_schema["required"]
    assert "content_md" in request_schema["properties"]
    assert {"session_id", "content_md", "is_edited", "updated_at"} <= set(
        response_schema["properties"]
    )


async def test_non_diarised_session_can_return_edited_markdown(
    db_session, make_db_session_dep
):
    plain = "gm-edit-non-diarised"
    gm = await _seed_gm(db_session, plain=plain)
    session = await _seed_session(
        db_session, gm_id=gm.id, mode=TranscriptionMode.NON_DIARISED
    )
    app = _make_jdr_app(make_db_session_dep)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        put = await client.put(
            f"/services/jdr/sessions/{session.id}/transcription",
            headers={"Authorization": f"Bearer {plain}"},
            json={"content_md": "Edited non diarised markdown"},
        )
        get = await client.get(
            f"/services/jdr/sessions/{session.id}/transcription.md",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert put.status_code == 200
    assert get.status_code == 200
    assert get.text == "Edited non diarised markdown"
    chunks = (
        await db_session.execute(
            select(Chunk).where(Chunk.session_id == session.id)
        )
    ).scalars().all()
    assert [chunk.text for chunk in chunks] == ["Automatic chunk text"]


async def test_put_transcription_edit_rejects_non_transcribed_session(
    db_session, make_db_session_dep
):
    plain = "gm-edit-not-ready"
    gm = await _seed_gm(db_session, plain=plain)
    session = await _seed_session(
        db_session, gm_id=gm.id, state=SessionState.AUDIO_UPLOADED
    )
    app = _make_jdr_app(make_db_session_dep)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.put(
            f"/services/jdr/sessions/{session.id}/transcription",
            headers={"Authorization": f"Bearer {plain}"},
            json={"content_md": "Too early"},
        )

    assert response.status_code == 409
    assert response.json()["type"].endswith("/session-not-transcribed")


async def test_put_transcription_edit_cross_owner_returns_404_and_preserves_content(
    db_session, make_db_session_dep
):
    owner_plain = "gm-edit-owner"
    other_plain = "gm-edit-other"
    owner = await _seed_gm(db_session, plain=owner_plain)
    await _seed_gm(db_session, plain=other_plain)
    session = await _seed_session(
        db_session, gm_id=owner.id, edited="Owner content"
    )
    app = _make_jdr_app(make_db_session_dep)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.put(
            f"/services/jdr/sessions/{session.id}/transcription",
            headers={"Authorization": f"Bearer {other_plain}"},
            json={"content_md": "Foreign overwrite"},
        )

    assert response.status_code == 404
    assert response.json()["type"].endswith("/session-not-found")
    await db_session.refresh(session)
    assert session.edited_transcript_md == "Owner content"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"content_md": None},
        {"content_md": ""},
        {"content_md": "   \n\t  "},
    ],
)
async def test_put_transcription_edit_rejects_invalid_content(
    db_session, make_db_session_dep, payload
):
    plain = "gm-edit-invalid"
    gm = await _seed_gm(db_session, plain=plain)
    session = await _seed_session(db_session, gm_id=gm.id)
    app = _make_jdr_app(make_db_session_dep)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.put(
            f"/services/jdr/sessions/{session.id}/transcription",
            headers={"Authorization": f"Bearer {plain}"},
            json=payload,
        )

    assert response.status_code == 422


async def test_put_transcription_edit_requires_gm_credentials(
    db_session, make_db_session_dep
):
    gm_plain = "gm-edit-auth"
    player_plain = "player-edit-auth"
    gm = await _seed_gm(db_session, plain=gm_plain)
    await _seed_player(db_session, plain=player_plain, gm_id=gm.id)
    session = await _seed_session(db_session, gm_id=gm.id)
    app = _make_jdr_app(make_db_session_dep)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        missing = await client.put(
            f"/services/jdr/sessions/{session.id}/transcription",
            json={"content_md": "No auth"},
        )
        player = await client.put(
            f"/services/jdr/sessions/{session.id}/transcription",
            headers={"Authorization": f"Bearer {player_plain}"},
            json={"content_md": "Player auth"},
        )

    assert missing.status_code == 401
    assert player.status_code == 403


async def test_put_transcription_edit_does_not_modify_automatic_sources(
    db_session, make_db_session_dep
):
    plain = "gm-edit-invariant"
    gm = await _seed_gm(db_session, plain=plain)
    diarised = await _seed_session(db_session, gm_id=gm.id)
    non_diarised = await _seed_session(
        db_session, gm_id=gm.id, mode=TranscriptionMode.NON_DIARISED
    )
    app = _make_jdr_app(make_db_session_dep)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.put(
            f"/services/jdr/sessions/{diarised.id}/transcription",
            headers={"Authorization": f"Bearer {plain}"},
            json={"content_md": "Edited diarised"},
        )
        await client.put(
            f"/services/jdr/sessions/{non_diarised.id}/transcription",
            headers={"Authorization": f"Bearer {plain}"},
            json={"content_md": "Edited non diarised"},
        )

    transcription = await db_session.scalar(
        select(Transcription).where(Transcription.session_id == diarised.id)
    )
    chunks = (
        await db_session.execute(
            select(Chunk).where(Chunk.session_id == non_diarised.id)
        )
    ).scalars().all()
    assert transcription is not None
    assert transcription.segments_json[0]["text"] == "Automatic diarised text"
    assert [chunk.text for chunk in chunks] == ["Automatic chunk text"]
