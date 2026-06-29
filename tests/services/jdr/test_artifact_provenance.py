"""Epic 8 / US3 (BD-24) -- manual-edit provenance + regeneration guard.

Manual edits must never be silently overwritten by an AI regeneration. The
route layer blocks destructive regeneration when an edited target/downstream
artifact exists, unless the caller passes ``?force=true``. The worker-level
tests here also lock the existing invariant that successful generation clears
manual provenance while failed summary regeneration preserves prior artifacts.
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

from app.adapters.llm import LLMAdapter, PermanentLLMError
from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.redis_client import get_redis
from app.jobs import PermanentJobError
from app.jobs.jdr import _generate_narrative, _generate_summary
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    Artifact,
    Campaign,
    Chunk,
    Pj,
    Role,
    Session,
    SessionPlayer,
    SessionState,
    TranscriptionMode,
)
from app.services.jdr.router import router as jdr_router


GENERATED_AT = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


@dataclass
class ProvenanceCtx:
    plain_token: str
    gm_key_id: UUID
    session_id: UUID
    pj_id: UUID
    sessionmaker: async_sessionmaker


class _StaticLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        idx = len(self.calls)
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        if idx < len(self._responses):
            return self._responses[idx]
        return self._responses[-1] if self._responses else ""


class _FailingLLM:
    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        raise PermanentLLMError("invalid_api_key: User is not authorized")


def _patch_llm(monkeypatch, adapter: LLMAdapter) -> None:
    monkeypatch.setattr("app.jobs.jdr.get_llm_adapter", lambda: adapter)


async def _seed_non_diarised_session(
    sm: async_sessionmaker,
    *,
    plain_token: str = "gm-provenance-token",
) -> ProvenanceCtx:
    session_id = uuid4()
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

        campaign = Campaign(name="Provenance campaign", owner_user_id=uuid4())
        setup.add(campaign)
        await setup.flush()

        pj = Pj(name="Mira", owner_gm_key_id=gm_id, campaign_id=campaign.id)
        setup.add(pj)
        await setup.flush()
        pj_id = pj.id

        setup.add(
            Session(
                id=session_id,
                title="Provenance session",
                recorded_at=datetime.now(UTC),
                gm_key_id=gm_id,
                campaign_id=campaign.id,
                state=SessionState.TRANSCRIBED,
                transcription_mode=TranscriptionMode.NON_DIARISED,
            )
        )
        setup.add(
            SessionPlayer(
                session_id=session_id,
                pj_id=pj_id,
            )
        )
        setup.add(
            Chunk(
                session_id=session_id,
                ordre=0,
                text="Le groupe explore une crypte.",
                summary_text="Ancien resume de chunk.",
            )
        )
        for kind, content in (
            ("summary", {"text": "Ancien resume global."}),
            ("narrative", {"text": "Ancien recit."}),
            (
                "elements",
                {
                    "elements": [
                        {
                            "category": "PNJ",
                            "name": "Archiviste",
                            "description": "Temoin important.",
                        }
                    ]
                },
            ),
            (f"pov:{pj_id}", {"text": "Ancien POV."}),
        ):
            setup.add(
                Artifact(
                    session_id=session_id,
                    kind=kind,
                    content_json=content,
                    model_used="old-model",
                    generated_at=GENERATED_AT,
                )
            )
        await setup.commit()

    return ProvenanceCtx(
        plain_token=plain_token,
        gm_key_id=gm_id,
        session_id=session_id,
        pj_id=pj_id,
        sessionmaker=sm,
    )


@pytest_asyncio.fixture
async def ctx(db_engine: AsyncEngine, monkeypatch) -> ProvenanceCtx:
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)
    return await _seed_non_diarised_session(sm)


def _make_jdr_app(make_db_session_dep: Callable[..., Any]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(jdr_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = lambda: fakeredis.FakeStrictRedis()
    return app


def _auth(ctx: ProvenanceCtx) -> dict[str, str]:
    return {"Authorization": f"Bearer {ctx.plain_token}"}


async def _mark_edited(ctx: ProvenanceCtx, kind: str) -> None:
    async with ctx.sessionmaker() as db:
        artifact = await db.get(Artifact, (ctx.session_id, kind))
        assert artifact is not None
        artifact.is_edited = True
        artifact.edited_at = datetime.now(UTC)
        artifact.edited_by = str(ctx.gm_key_id)
        await db.commit()


async def _artifact(ctx: ProvenanceCtx, kind: str) -> Artifact:
    async with ctx.sessionmaker() as db:
        artifact = await db.get(Artifact, (ctx.session_id, kind))
        assert artifact is not None
        return artifact


async def test_patch_summary_sets_provenance_and_keeps_generation_record(
    ctx, make_db_session_dep
):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/summary",
            headers=_auth(ctx),
            json={"text": "Resume corrige manuellement."},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["is_edited"] is True
    assert body["edited_at"] is not None
    assert body["edited_by"] == str(ctx.gm_key_id)
    assert body["model_used"] == "old-model"
    assert body["generated_at"] == GENERATED_AT.isoformat()


async def test_post_narrative_409_when_target_edited_without_force(
    ctx, make_db_session_dep
):
    await _mark_edited(ctx, "narrative")
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/narrative",
            headers=_auth(ctx),
        )

    assert resp.status_code == 409
    assert resp.json()["type"].endswith("/artifact-edited")


async def test_post_elements_409_when_target_edited_without_force(
    ctx, make_db_session_dep
):
    await _mark_edited(ctx, "elements")
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/elements",
            headers=_auth(ctx),
        )

    assert resp.status_code == 409
    assert resp.json()["type"].endswith("/artifact-edited")


async def test_post_povs_409_when_existing_pov_edited_without_force(
    ctx, make_db_session_dep
):
    await _mark_edited(ctx, f"pov:{ctx.pj_id}")
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/povs",
            headers=_auth(ctx),
        )

    assert resp.status_code == 409
    assert resp.json()["type"].endswith("/artifact-edited")


async def test_post_summary_409_when_summary_itself_edited_without_force(
    ctx, make_db_session_dep
):
    await _mark_edited(ctx, "summary")
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/summary",
            headers=_auth(ctx),
        )

    assert resp.status_code == 409
    assert resp.json()["type"].endswith("/artifact-edited")


async def test_post_summary_409_when_downstream_artifact_edited_without_force(
    ctx, make_db_session_dep
):
    await _mark_edited(ctx, "elements")
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/summary",
            headers=_auth(ctx),
        )

    assert resp.status_code == 409
    assert resp.json()["type"].endswith("/artifact-edited")


async def test_force_allows_regeneration_enqueue_without_immediate_deletion(
    ctx, make_db_session_dep
):
    await _mark_edited(ctx, "elements")
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/summary?force=true",
            headers=_auth(ctx),
        )

    assert resp.status_code == 202
    assert resp.json()["kind"] == "summary"
    # POST only queues the worker; destructive cascade still happens inside the
    # job after LLM success, so the edited artifact remains for now.
    assert (await _artifact(ctx, "elements")).is_edited is True


async def test_successful_generation_resets_manual_provenance(ctx, monkeypatch):
    await _mark_edited(ctx, "narrative")
    _patch_llm(monkeypatch, _StaticLLM(["Nouveau recit IA."]))

    await _generate_narrative(ctx.session_id)

    artifact = await _artifact(ctx, "narrative")
    assert artifact.content_json == {"text": "Nouveau recit IA."}
    assert artifact.is_edited is False
    assert artifact.edited_at is None
    assert artifact.edited_by is None


async def test_failed_summary_regeneration_preserves_edited_artifacts(
    ctx, monkeypatch
):
    await _mark_edited(ctx, "elements")
    _patch_llm(monkeypatch, _FailingLLM())

    with pytest.raises(PermanentJobError):
        await _generate_summary(ctx.session_id)

    artifact = await _artifact(ctx, "elements")
    assert artifact.content_json["elements"][0]["name"] == "Archiviste"
    assert artifact.is_edited is True

    async with ctx.sessionmaker() as db:
        chunks = (
            await db.execute(
                select(Chunk)
                .where(Chunk.session_id == ctx.session_id)
                .order_by(Chunk.ordre)
            )
        ).scalars().all()
    assert chunks[0].summary_text == "Ancien resume de chunk."
