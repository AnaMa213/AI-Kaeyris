"""US1 — Markdown rendering helpers + ``.md`` endpoints.

Two sides:
- Pure unit tests on ``app.services.jdr.markdown`` (no DB / no HTTP).
- HTTP tests on ``GET /sessions/{id}/transcription.md`` and
  ``GET /sessions/{id}/artifacts/narrative.md`` to check the
  Content-Type and the body shape.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import fakeredis
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.redis_client import get_redis
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    Artifact,
    Role,
    Session,
    SessionMode,
    SessionState,
    Transcription,
)
from app.services.jdr.markdown import (
    render_narrative_md,
    render_session_header,
    render_transcription_md,
)
from app.services.jdr.router import router as jdr_router


# ---------------------------------------------------------------------------
# Unit tests — pure rendering, no DB
# ---------------------------------------------------------------------------


def _fake_session(**overrides: Any) -> SimpleNamespace:
    """Stand-in for the ORM model, with attributes the renderer reads."""
    defaults = {
        "id": UUID("11111111-1111-1111-1111-111111111111"),
        "title": "Donjon des morts-vivants — chapitre 4",
        "recorded_at": datetime(2026, 5, 4, 20, 30, tzinfo=UTC),
        "mode": SessionMode.BATCH,
        "state": SessionState.TRANSCRIBED,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_render_session_header_contains_title_and_id():
    sess = _fake_session()
    md = render_session_header(sess)

    assert "Donjon des morts-vivants — chapitre 4" in md
    assert str(sess.id) in md
    assert "2026-05-04" in md  # recorded_at date is visible


def test_render_transcription_md_emits_paragraph_per_segment():
    sess = _fake_session()
    transcription = SimpleNamespace(
        segments_json=[
            {
                "speaker_label": "speaker_1",
                "start_seconds": 0.0,
                "end_seconds": 1.5,
                "text": "Bonjour à tous.",
            },
            {
                "speaker_label": "speaker_2",
                "start_seconds": 1.5,
                "end_seconds": 3.0,
                "text": "Salut.",
            },
        ],
        language="fr",
        provider="mock",
        model_used="mock:whisper",
    )

    md = render_transcription_md(sess, transcription)

    # The header is included so the file is self-contained.
    assert "Donjon des morts-vivants — chapitre 4" in md
    # One block per segment, prefixed with the speaker label.
    assert "speaker_1" in md
    assert "speaker_2" in md
    assert "Bonjour à tous." in md
    assert "Salut." in md
    # Provider attribution at the foot of the file.
    assert "mock" in md.lower()


def test_render_transcription_md_supports_pj_mapping():
    """When a mapping is provided (future US3), use PJ names instead of labels."""
    sess = _fake_session()
    transcription = SimpleNamespace(
        segments_json=[
            {
                "speaker_label": "speaker_1",
                "start_seconds": 0.0,
                "end_seconds": 1.0,
                "text": "Hello.",
            }
        ],
        language="fr",
        provider="mock",
        model_used="mock",
    )
    mapping = {"speaker_1": "Aragorn"}

    md = render_transcription_md(sess, transcription, mapping=mapping)

    assert "Aragorn" in md
    # The raw label still appears so the reader knows what was mapped.
    assert "speaker_1" in md


def test_render_transcription_md_handles_empty_segments():
    sess = _fake_session()
    transcription = SimpleNamespace(
        segments_json=[],
        language="fr",
        provider="mock",
        model_used="mock",
    )

    md = render_transcription_md(sess, transcription)
    assert "Donjon des morts-vivants" in md
    # Renderer doesn't crash — produces an explicit empty marker.
    assert "(aucun segment)" in md or "vide" in md.lower()


def test_render_narrative_md_contains_summary_text():
    sess = _fake_session()
    artifact = SimpleNamespace(
        content_json={"text": "Les héros descendirent dans le donjon..."},
        model_used="deepinfra:meta-llama/Meta-Llama-3.1-8B-Instruct",
        generated_at=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
    )

    md = render_narrative_md(sess, artifact)

    assert "Donjon des morts-vivants — chapitre 4" in md
    assert "Les héros descendirent dans le donjon..." in md
    # Model attribution at the foot of the file.
    assert "deepinfra" in md.lower() or "Llama" in md


def test_render_narrative_md_handles_missing_text():
    sess = _fake_session()
    artifact = SimpleNamespace(
        content_json={},  # malformed artefact
        model_used="mock",
        generated_at=datetime.now(UTC),
    )

    md = render_narrative_md(sess, artifact)
    # Doesn't crash — produces a clear placeholder.
    assert "(résumé vide)" in md or "(no narrative)" in md.lower()


# ---------------------------------------------------------------------------
# HTTP tests — full pipeline via the routes
# ---------------------------------------------------------------------------


@dataclass
class MdTestContext:
    plain_token: str
    session_id: UUID
    sessionmaker: async_sessionmaker


@pytest_asyncio.fixture
async def ctx_with_transcription(
    db_engine: AsyncEngine, monkeypatch
) -> MdTestContext:
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)

    plain = "gm-md-token"
    session_id = uuid4()
    async with sm() as setup:
        gm = ApiKey(
            name=f"gm-{uuid4().hex[:8]}",
            hash=PasswordHasher().hash(plain),
            role=Role.GM,
            status=ApiKeyStatus.ACTIVE,
        )
        setup.add(gm)
        await setup.flush()
        setup.add(
            Session(
                id=session_id,
                title="MD test session",
                recorded_at=datetime(2026, 5, 4, 20, 30, tzinfo=UTC),
                gm_key_id=gm.id,
                state=SessionState.TRANSCRIBED,
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
                        "text": "Bonjour à tous.",
                    },
                    {
                        "speaker_label": "speaker_2",
                        "start_seconds": 1.5,
                        "end_seconds": 3.0,
                        "text": "Salut.",
                    },
                ],
                language="fr",
                model_used="mock:whisper",
                provider="mock",
                completed_at=datetime.now(UTC),
            )
        )
        setup.add(
            Artifact(
                session_id=session_id,
                kind="narrative",
                content_json={"text": "Les héros descendirent dans le donjon."},
                model_used="mock:llm",
                generated_at=datetime.now(UTC),
            )
        )
        await setup.commit()

    return MdTestContext(plain_token=plain, session_id=session_id, sessionmaker=sm)


def _make_jdr_app(make_db_session_dep: Callable[..., Any]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(jdr_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = lambda: fakeredis.FakeStrictRedis()
    return app


async def test_get_transcription_md_returns_markdown(
    ctx_with_transcription, make_db_session_dep
):
    ctx = ctx_with_transcription
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{ctx.session_id}/transcription.md",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    body = response.text
    assert "MD test session" in body
    assert "speaker_1" in body
    assert "Bonjour à tous." in body
    assert "Salut." in body


async def test_get_transcription_md_returns_404_when_not_ready(
    db_engine: AsyncEngine, make_db_session_dep, monkeypatch
):
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)

    plain = "gm-md-no-tr"
    session_id = uuid4()
    async with sm() as setup:
        gm = ApiKey(
            name="gm-md-no-tr",
            hash=PasswordHasher().hash(plain),
            role=Role.GM,
            status=ApiKeyStatus.ACTIVE,
        )
        setup.add(gm)
        await setup.flush()
        setup.add(
            Session(
                id=session_id,
                title="No transcription yet",
                recorded_at=datetime.now(UTC),
                gm_key_id=gm.id,
                state=SessionState.AUDIO_UPLOADED,
            )
        )
        await setup.commit()

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{session_id}/transcription.md",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


async def test_get_narrative_md_returns_markdown(
    ctx_with_transcription, make_db_session_dep
):
    ctx = ctx_with_transcription
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/narrative.md",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    body = response.text
    assert "MD test session" in body
    assert "Les héros descendirent dans le donjon." in body


async def test_get_narrative_md_returns_404_when_not_generated(
    db_engine: AsyncEngine, make_db_session_dep, monkeypatch
):
    """Transcribed session without a narrative artefact yet."""
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)

    plain = "gm-md-no-nar"
    session_id = uuid4()
    async with sm() as setup:
        gm = ApiKey(
            name="gm-md-no-nar",
            hash=PasswordHasher().hash(plain),
            role=Role.GM,
            status=ApiKeyStatus.ACTIVE,
        )
        setup.add(gm)
        await setup.flush()
        setup.add(
            Session(
                id=session_id,
                title="No narrative yet",
                recorded_at=datetime.now(UTC),
                gm_key_id=gm.id,
                state=SessionState.TRANSCRIBED,
            )
        )
        await setup.commit()

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{session_id}/artifacts/narrative.md",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 404


async def test_md_endpoints_cross_tenant_returns_404(
    ctx_with_transcription, make_db_session_dep
):
    """Another GM cannot read the .md exports of a session that isn't theirs."""
    ctx = ctx_with_transcription
    plain_b = "another-gm-md-token"
    async with ctx.sessionmaker() as db:
        db.add(
            ApiKey(
                name="another-gm-md",
                hash=PasswordHasher().hash(plain_b),
                role=Role.GM,
                status=ApiKeyStatus.ACTIVE,
            )
        )
        await db.commit()

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp_tr = await client.get(
            f"/services/jdr/sessions/{ctx.session_id}/transcription.md",
            headers={"Authorization": f"Bearer {plain_b}"},
        )
        resp_na = await client.get(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/narrative.md",
            headers={"Authorization": f"Bearer {plain_b}"},
        )

    assert resp_tr.status_code == 404
    assert resp_na.status_code == 404
