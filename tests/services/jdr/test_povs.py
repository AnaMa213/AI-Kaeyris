"""US3 / sub-lot 5b — POV generation + retrieval per PJ.

The POV pipeline mirrors :mod:`tests.services.jdr.test_narrative` but
produces *one row per mapped PJ* (kind ``pov:<pj_id>``) instead of a
single narrative artefact.

What we cover here (T047):
- ``_generate_povs(session_id)`` writes one ``Artifact`` per mapped PJ.
- The POST endpoint enqueues a job and returns 202 + ``JobQueuedOut``.
- The GET ``.md`` endpoint returns a Markdown rendering scoped to the
  requested PJ (its name appears in the document).

The "no mapping → 409" case is covered separately in
:mod:`tests.services.jdr.test_povs_no_mapping` (T048).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import fakeredis
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.adapters.llm import LLMAdapter
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
    SessionState,
    Transcription,
)
from app.services.jdr.router import router as jdr_router


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _StubLLM:
    """Captures the prompts and returns a per-call deterministic POV.

    Index-based — ``responses[i]`` is returned for the i-th call. Useful
    to differentiate the per-PJ responses in assertions.
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        idx = len(self.calls)
        self.calls.append(
            {"system": system, "user": user, "max_tokens": max_tokens}
        )
        if idx < len(self._responses):
            return self._responses[idx]
        return self._responses[-1] if self._responses else ""


def _patch_llm_adapter(monkeypatch, adapter: LLMAdapter) -> None:
    monkeypatch.setattr("app.jobs.jdr.get_llm_adapter", lambda: adapter)


# ---------------------------------------------------------------------------
# Context: GM + 2 PJs + transcribed Session + Transcription + Mapping
# ---------------------------------------------------------------------------


@dataclass
class PovTestContext:
    plain_token: str
    gm_key_id: UUID
    pj_galadriel_id: UUID
    pj_aragorn_id: UUID
    session_id: UUID
    sessionmaker: async_sessionmaker


async def _seed_session_with_mapping(
    sm: async_sessionmaker,
    *,
    plain_token: str = "gm-pov-token",
) -> PovTestContext:
    """GM + 2 PJ + Session(transcribed) + Transcription + Mapping.

    Mapping: speaker_1 -> Galadriel, speaker_2 -> Aragorn.
    """
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

        campaign = Campaign(name="POV campaign", owner_user_id=uuid4())
        setup.add(campaign)
        await setup.flush()

        pj_galadriel = Pj(
            name="Galadriel", owner_gm_key_id=gm_id, campaign_id=campaign.id
        )
        pj_aragorn = Pj(
            name="Aragorn", owner_gm_key_id=gm_id, campaign_id=campaign.id
        )
        setup.add(pj_galadriel)
        setup.add(pj_aragorn)
        await setup.flush()
        pj_galadriel_id = pj_galadriel.id
        pj_aragorn_id = pj_aragorn.id

        setup.add(
            Session(
                id=session_id,
                title="POV test session",
                recorded_at=datetime.now(UTC),
                gm_key_id=gm_id,
                campaign_id=campaign.id,
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
                        "end_seconds": 2.0,
                        "text": "Je sens une présence ancienne dans la forêt.",
                    },
                    {
                        "speaker_label": "speaker_2",
                        "start_seconds": 2.0,
                        "end_seconds": 4.0,
                        "text": "Préparons les chevaux, nous partons à l'aube.",
                    },
                ],
                language="fr",
                model_used="mock:whisper",
                provider="mock",
                completed_at=datetime.now(UTC),
            )
        )
        setup.add(
            SessionPjMapping(
                session_id=session_id,
                speaker_label="speaker_1",
                pj_id=pj_galadriel_id,
            )
        )
        setup.add(
            SessionPjMapping(
                session_id=session_id,
                speaker_label="speaker_2",
                pj_id=pj_aragorn_id,
            )
        )
        await setup.commit()

    return PovTestContext(
        plain_token=plain_token,
        gm_key_id=gm_id,
        pj_galadriel_id=pj_galadriel_id,
        pj_aragorn_id=pj_aragorn_id,
        session_id=session_id,
        sessionmaker=sm,
    )


@pytest_asyncio.fixture
async def ctx(db_engine: AsyncEngine, monkeypatch) -> PovTestContext:
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)
    return await _seed_session_with_mapping(sm)


def _make_jdr_app(make_db_session_dep: Callable[..., Any]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(jdr_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = lambda: fakeredis.FakeStrictRedis()
    return app


# ---------------------------------------------------------------------------
# Job-level tests
# ---------------------------------------------------------------------------


async def test_generate_povs_writes_one_artifact_per_mapped_pj(ctx, monkeypatch):
    """One row per mapped (speaker, pj) — exactly two here."""
    stub = _StubLLM(
        [
            "POV de Galadriel — version mock.",
            "POV d'Aragorn — version mock.",
        ]
    )
    _patch_llm_adapter(monkeypatch, stub)

    from app.jobs.jdr import _generate_povs

    await _generate_povs(ctx.session_id)

    async with ctx.sessionmaker() as db:
        rows = (
            await db.execute(
                select(Artifact)
                .where(Artifact.session_id == ctx.session_id)
                .where(Artifact.kind.like("pov:%"))
            )
        ).scalars().all()
    kinds = sorted(r.kind for r in rows)
    assert kinds == sorted(
        [f"pov:{ctx.pj_galadriel_id}", f"pov:{ctx.pj_aragorn_id}"]
    )
    assert len(stub.calls) == 2


async def test_generate_povs_uses_pov_system_prompt(ctx, monkeypatch):
    """Each LLM call must use ``POV_SYSTEM_PROMPT`` and embed the PJ name."""
    stub = _StubLLM(["POV-1", "POV-2"])
    _patch_llm_adapter(monkeypatch, stub)

    from app.jobs.jdr import _generate_povs
    from app.services.jdr.prompts import POV_SYSTEM_PROMPT

    await _generate_povs(ctx.session_id)

    assert POV_SYSTEM_PROMPT.strip() != "", "POV_SYSTEM_PROMPT must be defined."
    for call in stub.calls:
        assert call["system"] == POV_SYSTEM_PROMPT
    # The PJ name must appear in the corresponding user prompt so the LLM
    # knows whose perspective to take.
    all_user_prompts = " ".join(c["user"] for c in stub.calls)
    assert "Galadriel" in all_user_prompts
    assert "Aragorn" in all_user_prompts


# ---------------------------------------------------------------------------
# Endpoint-level tests
# ---------------------------------------------------------------------------


async def test_post_povs_enqueues_job_returns_202(
    ctx, make_db_session_dep
):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/povs",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert response.status_code == 202
    body = response.json()
    assert "id" in body  # job id
    assert body.get("kind") == "povs"
    assert body.get("session_id") == str(ctx.session_id)


async def test_get_pov_md_returns_scoped_markdown(ctx, monkeypatch):
    """``GET /artifacts/povs/{pj_id}.md`` returns the PJ's POV as MD."""
    stub = _StubLLM(
        [
            "Du point de vue de Galadriel : la forêt résonne d'échos anciens.",
            "Du point de vue d'Aragorn : la route nous attend à l'aube.",
        ]
    )
    _patch_llm_adapter(monkeypatch, stub)

    from app.jobs.jdr import _generate_povs

    await _generate_povs(ctx.session_id)

    # Now fetch the Galadriel POV via the route — we expose make_db_session_dep
    # by re-using the same engine as ctx.sessionmaker via a fresh dep override.
    sessionmaker = ctx.sessionmaker

    async def _dep():
        async with sessionmaker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app = _make_jdr_app(_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/povs/{ctx.pj_galadriel_id}.md",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    body = response.text
    assert "Galadriel" in body
    assert "forêt résonne" in body
    # Aragorn's POV must NOT leak into Galadriel's MD — the LLM mock
    # never mentions Aragorn in Galadriel's response.
    assert "Aragorn" not in body
