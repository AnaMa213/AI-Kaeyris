"""US1 — Narrative artifact generation.

POST /sessions/{id}/artifacts/narrative enqueues a job that builds a
French narrative summary of the session via the LLMAdapter. GET returns
the JSON artifact. 409 if the session is not yet transcribed.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import fakeredis
import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.adapters.llm import (
    LLMAdapter,
    PermanentLLMError,
    TransientLLMError,
)
from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.redis_client import get_redis
from app.jobs import PermanentJobError, TransientJobError
from app.jobs.jdr import _generate_narrative
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    Artifact,
    Role,
    Session,
    SessionState,
    Transcription,
)
from app.services.jdr.router import router as jdr_router


# ---------------------------------------------------------------------------
# Test doubles + context
# ---------------------------------------------------------------------------


@dataclass
class NarrativeTestContext:
    plain_token: str
    gm_key_id: UUID
    session_id: UUID
    sessionmaker: async_sessionmaker


class _StubLLM:
    """Captures the prompts and returns a fixed narrative."""

    def __init__(self, narrative: str = "Récit de la session — version mock.") -> None:
        self.narrative = narrative
        self.last_system: str | None = None
        self.last_user: str | None = None
        self.last_max_tokens: int | None = None

    async def complete(
        self, *, system: str, user: str, max_tokens: int
    ) -> str:
        self.last_system = system
        self.last_user = user
        self.last_max_tokens = max_tokens
        return self.narrative


class _RaisingLLM:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        raise self._exc


def _patch_llm_adapter(monkeypatch, adapter: LLMAdapter):
    monkeypatch.setattr("app.jobs.jdr.get_llm_adapter", lambda: adapter)


async def _seed_transcribed_session(
    sm: async_sessionmaker,
    *,
    plain_token: str = "gm-narrative-token",
    segments: list[dict] | None = None,
) -> NarrativeTestContext:
    """Insert GM + Session(state=transcribed) + Transcription."""
    if segments is None:
        segments = [
            {
                "speaker_label": "speaker_1",
                "start_seconds": 0.0,
                "end_seconds": 1.5,
                "text": "On entre dans le donjon.",
            },
            {
                "speaker_label": "speaker_2",
                "start_seconds": 1.5,
                "end_seconds": 3.0,
                "text": "Je dégaine mon épée.",
            },
        ]
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
                title="Narrative test",
                recorded_at=datetime.now(UTC),
                gm_key_id=gm_id,
                state=SessionState.TRANSCRIBED,
            )
        )
        setup.add(
            Transcription(
                session_id=session_id,
                segments_json=segments,
                language="fr",
                model_used="mock:whisper",
                provider="mock",
                completed_at=datetime.now(UTC),
            )
        )
        await setup.commit()

    return NarrativeTestContext(
        plain_token=plain_token,
        gm_key_id=gm_id,
        session_id=session_id,
        sessionmaker=sm,
    )


@pytest_asyncio.fixture
async def ctx(db_engine: AsyncEngine, monkeypatch) -> NarrativeTestContext:
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)
    return await _seed_transcribed_session(sm)


@pytest_asyncio.fixture
async def ctx_not_transcribed(
    db_engine: AsyncEngine, monkeypatch
) -> NarrativeTestContext:
    """A session in audio_uploaded state — narrative cannot run yet."""
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)

    plain = "gm-not-yet-token"
    session_id = uuid4()
    gm_id: UUID
    async with sm() as setup:
        gm = ApiKey(
            name=f"gm-{uuid4().hex[:8]}",
            hash=PasswordHasher().hash(plain),
            role=Role.GM,
            status=ApiKeyStatus.ACTIVE,
        )
        setup.add(gm)
        await setup.flush()
        gm_id = gm.id
        setup.add(
            Session(
                id=session_id,
                title="Not yet transcribed",
                recorded_at=datetime.now(UTC),
                gm_key_id=gm_id,
                state=SessionState.AUDIO_UPLOADED,
            )
        )
        await setup.commit()
    return NarrativeTestContext(
        plain_token=plain,
        gm_key_id=gm_id,
        session_id=session_id,
        sessionmaker=sm,
    )


def _make_jdr_app(make_db_session_dep: Callable[..., Any]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(jdr_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = lambda: fakeredis.FakeStrictRedis()
    return app


# ---------------------------------------------------------------------------
# Job: happy path + behaviour
# ---------------------------------------------------------------------------


async def test_generate_narrative_writes_artifact(ctx, monkeypatch):
    stub = _StubLLM("Le récit en français généré par le mock.")
    _patch_llm_adapter(monkeypatch, stub)

    await _generate_narrative(ctx.session_id)

    async with ctx.sessionmaker() as db:
        artifact = await db.scalar(
            select(Artifact).where(
                Artifact.session_id == ctx.session_id,
                Artifact.kind == "narrative",
            )
        )
        assert artifact is not None
        assert artifact.content_json == {"text": "Le récit en français généré par le mock."}
        assert "mock" in artifact.model_used.lower() or artifact.model_used


async def test_generate_narrative_calls_llm_with_system_prompt(
    ctx, monkeypatch
):
    stub = _StubLLM()
    _patch_llm_adapter(monkeypatch, stub)

    await _generate_narrative(ctx.session_id)

    # The system prompt must come from prompts.NARRATIVE_SYSTEM_PROMPT and
    # be a non-empty French instruction about narrative summarisation.
    assert stub.last_system is not None
    assert stub.last_system.strip() != ""
    # The user prompt embeds the segments somehow (we don't pin the format).
    assert stub.last_user is not None
    assert "donjon" in stub.last_user.lower()
    assert "épée" in stub.last_user.lower()


async def test_generate_narrative_is_idempotent(ctx, monkeypatch):
    stub_first = _StubLLM("first run")
    _patch_llm_adapter(monkeypatch, stub_first)
    await _generate_narrative(ctx.session_id)

    stub_second = _StubLLM("second run — overwrites")
    _patch_llm_adapter(monkeypatch, stub_second)
    await _generate_narrative(ctx.session_id)

    async with ctx.sessionmaker() as db:
        artifacts = (
            await db.scalars(
                select(Artifact).where(
                    Artifact.session_id == ctx.session_id,
                    Artifact.kind == "narrative",
                )
            )
        ).all()
    assert len(artifacts) == 1  # composite PK (session_id, kind) — UPSERT
    assert artifacts[0].content_json == {"text": "second run — overwrites"}


# ---------------------------------------------------------------------------
# Job: error mapping
# ---------------------------------------------------------------------------


async def test_generate_narrative_remaps_transient_llm_error(ctx, monkeypatch):
    _patch_llm_adapter(monkeypatch, _RaisingLLM(TransientLLMError("503")))

    with pytest.raises(TransientJobError, match="503"):
        await _generate_narrative(ctx.session_id)


async def test_generate_narrative_remaps_permanent_llm_error(ctx, monkeypatch):
    _patch_llm_adapter(monkeypatch, _RaisingLLM(PermanentLLMError("invalid prompt")))

    with pytest.raises(PermanentJobError, match="invalid prompt"):
        await _generate_narrative(ctx.session_id)


async def test_generate_narrative_unknown_session_raises_permanent(
    db_engine, monkeypatch
):
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)
    _patch_llm_adapter(monkeypatch, _StubLLM())

    with pytest.raises(PermanentJobError, match="not found"):
        await _generate_narrative(uuid4())


async def test_generate_narrative_refuses_when_not_transcribed(
    ctx_not_transcribed, monkeypatch
):
    _patch_llm_adapter(monkeypatch, _StubLLM())

    with pytest.raises(PermanentJobError, match=r"(?i)not transcribed|transcrib"):
        await _generate_narrative(ctx_not_transcribed.session_id)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


async def test_post_narrative_returns_202_with_job_id(
    ctx, make_db_session_dep, monkeypatch
):
    _patch_llm_adapter(monkeypatch, _StubLLM())
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/narrative",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["kind"] == "narrative"
    assert body["session_id"] == str(ctx.session_id)
    assert body["status"] == "queued"
    assert "id" in body and isinstance(body["id"], str)


async def test_post_narrative_returns_409_when_session_not_transcribed(
    ctx_not_transcribed, make_db_session_dep
):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/services/jdr/sessions/{ctx_not_transcribed.session_id}/artifacts/narrative",
            headers={"Authorization": f"Bearer {ctx_not_transcribed.plain_token}"},
        )

    assert response.status_code == 409
    body = response.json()
    assert body["type"].endswith("/session-not-transcribed")


async def test_post_narrative_returns_404_for_unknown_session(
    ctx, make_db_session_dep
):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    unknown = "00000000-0000-0000-0000-000000000000"

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/services/jdr/sessions/{unknown}/artifacts/narrative",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert response.status_code == 404


async def test_get_narrative_returns_200_after_generation(
    ctx, make_db_session_dep, monkeypatch
):
    stub = _StubLLM("Texte narratif produit par le mock.")
    _patch_llm_adapter(monkeypatch, stub)
    await _generate_narrative(ctx.session_id)

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/narrative",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == str(ctx.session_id)
    assert body["text"] == "Texte narratif produit par le mock."
    assert "generated_at" in body
    assert "model_used" in body


async def test_get_narrative_returns_404_when_not_generated(
    ctx, make_db_session_dep
):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/narrative",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert response.status_code == 404
    body = response.json()
    assert body["type"].endswith("/artifact-not-ready")


async def test_get_narrative_cross_tenant_returns_404(
    ctx, make_db_session_dep, monkeypatch
):
    _patch_llm_adapter(monkeypatch, _StubLLM())
    await _generate_narrative(ctx.session_id)

    plain_b = "another-gm-token"
    async with ctx.sessionmaker() as db:
        db.add(
            ApiKey(
                name="another-gm-for-narrative",
                hash=PasswordHasher().hash(plain_b),
                role=Role.GM,
                status=ApiKeyStatus.ACTIVE,
            )
        )
        await db.commit()

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/narrative",
            headers={"Authorization": f"Bearer {plain_b}"},
        )

    assert response.status_code == 404
