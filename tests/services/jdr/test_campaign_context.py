"""Lot 4c — campaign_context persisted per session + PATCH /sessions/{id}.

Covers:
- POST /sessions accepts ``campaign_context`` at creation.
- GET /sessions/{id} echoes the field.
- PATCH /sessions/{id} updates title and/or campaign_context, with
  proper PATCH semantics (missing key = leave alone, explicit null = clear).
- _generate_narrative and _generate_elements inject the context into the
  user prompt; without it the prompt is unchanged.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import fakeredis
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.adapters.llm import LLMAdapter
from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.redis_client import get_redis
from app.jobs.jdr import (
    _build_user_prompt_with_context,
    _generate_elements,
    _generate_narrative,
)
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    Role,
    Session,
    SessionState,
    Transcription,
)
from app.services.jdr.router import router as jdr_router


# ---------------------------------------------------------------------------
# _build_user_prompt_with_context — pure unit tests
# ---------------------------------------------------------------------------


def test_prompt_helper_passthrough_when_no_context():
    out = _build_user_prompt_with_context(None, "[0.0s] speaker_1 : hello")
    assert out == "[0.0s] speaker_1 : hello"


def test_prompt_helper_passthrough_when_context_is_blank():
    """Whitespace-only context is treated as missing — no empty wrapper."""
    out = _build_user_prompt_with_context("   \n\t  ", "transcript here")
    assert out == "transcript here"


def test_prompt_helper_wraps_context_and_transcript_in_distinct_blocks():
    out = _build_user_prompt_with_context(
        "Campagne : Terres du Milieu, ton sombre", "[0.0s] sp1 : transcript line"
    )
    # Both blocks are present, clearly labelled in French.
    assert "CONTEXTE DE CAMPAGNE" in out
    assert "TRANSCRIPTION DE LA SESSION" in out
    assert "Terres du Milieu" in out
    assert "transcript line" in out
    # Context appears before transcript.
    assert out.index("CONTEXTE DE CAMPAGNE") < out.index("TRANSCRIPTION DE LA SESSION")


# ---------------------------------------------------------------------------
# POST + GET — sessions carry the field
# ---------------------------------------------------------------------------


def _make_jdr_app(make_db_session_dep: Callable[..., Any]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(jdr_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = lambda: fakeredis.FakeStrictRedis()
    return app


@pytest_asyncio.fixture
async def gm_token(db_session) -> str:
    plain = "gm-context-token"
    db_session.add(
        ApiKey(
            name=f"gm-{uuid4().hex[:8]}",
            hash=PasswordHasher().hash(plain),
            role=Role.GM,
            status=ApiKeyStatus.ACTIVE,
        )
    )
    await db_session.commit()
    return plain


async def test_post_session_accepts_campaign_context(
    gm_token, make_db_session_dep
):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/services/jdr/sessions",
            json={
                "title": "Session avec contexte",
                "recorded_at": "2026-05-13T19:00:00+00:00",
                "campaign_context": "Campagne : Terres du Milieu.\nPJ : Frodon, Aragorn.",
            },
            headers={"Authorization": f"Bearer {gm_token}"},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["campaign_context"].startswith("Campagne : Terres du Milieu")


async def test_post_session_works_without_campaign_context(
    gm_token, make_db_session_dep
):
    """Field is optional — omitting it keeps the previous behaviour."""
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/services/jdr/sessions",
            json={
                "title": "Session sans contexte",
                "recorded_at": "2026-05-13T19:00:00+00:00",
            },
            headers={"Authorization": f"Bearer {gm_token}"},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["campaign_context"] is None


async def test_get_session_returns_campaign_context(
    gm_token, make_db_session_dep
):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create = await client.post(
            "/services/jdr/sessions",
            json={
                "title": "Read back",
                "recorded_at": "2026-05-13T19:00:00+00:00",
                "campaign_context": "Bible courte.",
            },
            headers={"Authorization": f"Bearer {gm_token}"},
        )
        session_id = create.json()["id"]

        fetched = await client.get(
            f"/services/jdr/sessions/{session_id}",
            headers={"Authorization": f"Bearer {gm_token}"},
        )

    assert fetched.status_code == 200
    assert fetched.json()["campaign_context"] == "Bible courte."


# ---------------------------------------------------------------------------
# PATCH /sessions/{id}
# ---------------------------------------------------------------------------


async def test_patch_session_updates_title_only(gm_token, make_db_session_dep):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create = await client.post(
            "/services/jdr/sessions",
            json={
                "title": "Original",
                "recorded_at": "2026-05-13T19:00:00+00:00",
                "campaign_context": "Bible intacte.",
            },
            headers={"Authorization": f"Bearer {gm_token}"},
        )
        session_id = create.json()["id"]

        patch = await client.patch(
            f"/services/jdr/sessions/{session_id}",
            json={"title": "Renommée"},
            headers={"Authorization": f"Bearer {gm_token}"},
        )

    assert patch.status_code == 200
    body = patch.json()
    assert body["title"] == "Renommée"
    # campaign_context was not in the payload -> left alone.
    assert body["campaign_context"] == "Bible intacte."


async def test_patch_session_updates_campaign_context(
    gm_token, make_db_session_dep
):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create = await client.post(
            "/services/jdr/sessions",
            json={
                "title": "Same title",
                "recorded_at": "2026-05-13T19:00:00+00:00",
                "campaign_context": "v1",
            },
            headers={"Authorization": f"Bearer {gm_token}"},
        )
        session_id = create.json()["id"]

        patch = await client.patch(
            f"/services/jdr/sessions/{session_id}",
            json={"campaign_context": "v2 enrichi"},
            headers={"Authorization": f"Bearer {gm_token}"},
        )

    assert patch.status_code == 200
    assert patch.json()["campaign_context"] == "v2 enrichi"


async def test_patch_session_clears_campaign_context_with_explicit_null(
    gm_token, make_db_session_dep
):
    """Sending an explicit ``null`` is how the caller wipes the field."""
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create = await client.post(
            "/services/jdr/sessions",
            json={
                "title": "To be cleared",
                "recorded_at": "2026-05-13T19:00:00+00:00",
                "campaign_context": "à effacer",
            },
            headers={"Authorization": f"Bearer {gm_token}"},
        )
        session_id = create.json()["id"]

        patch = await client.patch(
            f"/services/jdr/sessions/{session_id}",
            json={"campaign_context": None},
            headers={"Authorization": f"Bearer {gm_token}"},
        )

    assert patch.status_code == 200
    assert patch.json()["campaign_context"] is None


async def test_patch_session_returns_404_for_unknown_session(
    gm_token, make_db_session_dep
):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    unknown = "00000000-0000-0000-0000-000000000000"
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        patch = await client.patch(
            f"/services/jdr/sessions/{unknown}",
            json={"title": "x"},
            headers={"Authorization": f"Bearer {gm_token}"},
        )
    assert patch.status_code == 404


async def test_patch_session_cross_tenant_returns_404(
    gm_token, make_db_session_dep, db_session
):
    """Another MJ can't patch session of GM A (FR-014)."""
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create = await client.post(
            "/services/jdr/sessions",
            json={
                "title": "GM A's session",
                "recorded_at": "2026-05-13T19:00:00+00:00",
            },
            headers={"Authorization": f"Bearer {gm_token}"},
        )
        session_id = create.json()["id"]

    plain_b = "gm-b-context"
    db_session.add(
        ApiKey(
            name="gm-b-context",
            hash=PasswordHasher().hash(plain_b),
            role=Role.GM,
            status=ApiKeyStatus.ACTIVE,
        )
    )
    await db_session.commit()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        patch = await client.patch(
            f"/services/jdr/sessions/{session_id}",
            json={"title": "hijack attempt"},
            headers={"Authorization": f"Bearer {plain_b}"},
        )
    assert patch.status_code == 404
    assert patch.json()["type"].endswith("/session-not-found")


# ---------------------------------------------------------------------------
# Context injection into LLM jobs
# ---------------------------------------------------------------------------


class _CapturingLLM:
    def __init__(self, payload: str = "ok"):
        self.payload = payload
        self.last_user: str | None = None

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        self.last_user = user
        return self.payload


def _patch_llm(monkeypatch, adapter: LLMAdapter):
    monkeypatch.setattr("app.jobs.jdr.get_llm_adapter", lambda: adapter)


async def _seed_transcribed(
    sm: async_sessionmaker,
    *,
    plain_token: str,
    campaign_context: str | None = None,
) -> tuple[UUID, UUID]:
    session_id = uuid4()
    gm_id: UUID
    async with sm() as setup:
        gm = ApiKey(
            name=f"gm-{uuid4().hex[:8]}",
            hash=PasswordHasher().hash(plain_token),
            role=Role.GM,
            status=ApiKeyStatus.ACTIVE,
        )
        setup.add(gm)
        await setup.flush()
        gm_id = gm.id
        setup.add(
            Session(
                id=session_id,
                title="ctx test",
                recorded_at=datetime.now(UTC),
                gm_key_id=gm_id,
                state=SessionState.TRANSCRIBED,
                campaign_context=campaign_context,
            )
        )
        setup.add(
            Transcription(
                session_id=session_id,
                segments_json=[
                    {
                        "speaker_label": "speaker_1",
                        "start_seconds": 0.0,
                        "end_seconds": 1.5,
                        "text": "On entre dans la salle.",
                    },
                ],
                language="fr",
                model_used="mock:whisper",
                provider="mock",
                completed_at=datetime.now(UTC),
            )
        )
        await setup.commit()
    return session_id, gm_id


async def test_generate_narrative_injects_campaign_context_when_present(
    db_engine: AsyncEngine, monkeypatch
):
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)
    session_id, _ = await _seed_transcribed(
        sm,
        plain_token="gm-narr-ctx",
        campaign_context="Terres du Milieu. PJ : Frodon, Sam.",
    )
    captor = _CapturingLLM()
    _patch_llm(monkeypatch, captor)

    await _generate_narrative(session_id)

    assert captor.last_user is not None
    assert "CONTEXTE DE CAMPAGNE" in captor.last_user
    assert "Terres du Milieu" in captor.last_user
    assert "TRANSCRIPTION DE LA SESSION" in captor.last_user


async def test_generate_narrative_skips_context_block_when_unset(
    db_engine: AsyncEngine, monkeypatch
):
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)
    session_id, _ = await _seed_transcribed(
        sm, plain_token="gm-narr-no-ctx", campaign_context=None
    )
    captor = _CapturingLLM()
    _patch_llm(monkeypatch, captor)

    await _generate_narrative(session_id)

    assert captor.last_user is not None
    assert "CONTEXTE DE CAMPAGNE" not in captor.last_user


async def test_generate_elements_injects_campaign_context_when_present(
    db_engine: AsyncEngine, monkeypatch
):
    """The same wiring must hold for the elements job (Lot 4 / US2)."""
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)
    session_id, _ = await _seed_transcribed(
        sm,
        plain_token="gm-elem-ctx",
        campaign_context="Univers steampunk. PNJ récurrent : Inspecteur Drood.",
    )
    captor = _CapturingLLM('{"npcs":[],"locations":[],"items":[],"clues":[]}')
    _patch_llm(monkeypatch, captor)

    await _generate_elements(session_id)

    assert captor.last_user is not None
    assert "CONTEXTE DE CAMPAGNE" in captor.last_user
    assert "Inspecteur Drood" in captor.last_user


# Static-checker silencer for the imported UUID alias.
_ = UUID
