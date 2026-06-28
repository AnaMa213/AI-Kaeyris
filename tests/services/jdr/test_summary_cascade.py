"""US2 / feature 002 — FR-011 cascade invalidation atomique.

À la régénération du `summary`, le job DOIT, atomiquement :
1. Reset `chunks.summary_text` à NULL pour tous les chunks de la session.
2. Delete cascade les artefacts `narrative`, `elements`, `pov:*`.
3. Puis relance le map+reduce.

C'est le test critique de la séquence atomique décrite dans
research.md §2 et data-model.md §6.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest_asyncio
from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

import pytest

from app.adapters.llm import LLMAdapter, PermanentLLMError
from app.jobs import PermanentJobError
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    Artifact,
    Campaign,
    Chunk,
    Role,
    Session,
    SessionState,
    TranscriptionMode,
)


@dataclass
class CascadeCtx:
    session_id: UUID
    pj_ids: list[UUID]
    sessionmaker: async_sessionmaker


class _StaticStubLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        idx = len(self.calls)
        self.calls.append({"system": system, "user": user})
        if idx < len(self._responses):
            return self._responses[idx]
        return self._responses[-1] if self._responses else ""


def _patch_llm(monkeypatch, adapter: LLMAdapter) -> None:
    monkeypatch.setattr("app.jobs.jdr.get_llm_adapter", lambda: adapter)


@pytest_asyncio.fixture
async def ctx_with_existing_derived_artefacts(
    db_engine: AsyncEngine, monkeypatch
) -> CascadeCtx:
    """Seed une session non_diarised avec :
    - 2 chunks ayant déjà des summary_text "anciens"
    - 1 artefact narrative existant
    - 1 artefact elements existant
    - 2 artefacts pov:<pj_id> existants
    - 1 artefact summary "ancien"
    """
    from app.services.jdr.db.models import Pj

    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)

    session_id = uuid4()
    pj_a_id = uuid4()
    pj_b_id = uuid4()
    gm_id: UUID

    async with sm() as setup:
        gm = ApiKey(
            name=f"gm-cascade-{uuid4().hex[:8]}",
            hash=PasswordHasher().hash("noop"),
            role=Role.GM,
            status=ApiKeyStatus.ACTIVE,
        )
        setup.add(gm)
        await setup.flush()
        gm_id = gm.id

        campaign = Campaign(name="Cascade campaign", owner_user_id=uuid4())
        setup.add(campaign)
        await setup.flush()

        pj_a = Pj(
            id=pj_a_id,
            name="A",
            owner_gm_key_id=gm_id,
            campaign_id=campaign.id,
        )
        pj_b = Pj(
            id=pj_b_id,
            name="B",
            owner_gm_key_id=gm_id,
            campaign_id=campaign.id,
        )
        setup.add(pj_a)
        setup.add(pj_b)

        setup.add(
            Session(
                id=session_id,
                title="Cascade test",
                recorded_at=datetime.now(UTC),
                gm_key_id=gm_id,
                campaign_id=campaign.id,
                state=SessionState.TRANSCRIBED,
                transcription_mode=TranscriptionMode.NON_DIARISED,
            )
        )
        setup.add(
            Chunk(
                session_id=session_id,
                ordre=0,
                text="Chunk 0 contenu.",
                summary_text="ancien résumé 0",
            )
        )
        setup.add(
            Chunk(
                session_id=session_id,
                ordre=1,
                text="Chunk 1 contenu.",
                summary_text="ancien résumé 1",
            )
        )
        # Artefacts dérivés existants
        for kind, payload in (
            ("summary", {"text": "ancien résumé global"}),
            ("narrative", {"text": "ancien narrative"}),
            ("elements", {"npcs": [], "locations": [], "items": [], "clues": []}),
            (f"pov:{pj_a_id}", {"text": "ancien POV A"}),
            (f"pov:{pj_b_id}", {"text": "ancien POV B"}),
        ):
            setup.add(
                Artifact(
                    session_id=session_id,
                    kind=kind,
                    content_json=payload,
                    model_used="old-model",
                )
            )
        await setup.commit()

    return CascadeCtx(
        session_id=session_id,
        pj_ids=[pj_a_id, pj_b_id],
        sessionmaker=sm,
    )


# ---------------------------------------------------------------------------
# Cascade invalidation : reset summary_text + DELETE narrative/elements/pov:*
# ---------------------------------------------------------------------------


async def test_regenerate_summary_cascade_resets_chunks_and_deletes_derived(
    ctx_with_existing_derived_artefacts, monkeypatch
):
    """FR-011 : reset complet de l'état dérivé avant le nouveau map-reduce."""
    stub = _StaticStubLLM(
        responses=["nouveau résumé chunk 0", "nouveau résumé chunk 1", "nouveau résumé global"]
    )
    _patch_llm(monkeypatch, stub)

    from app.jobs.jdr import _generate_summary

    await _generate_summary(ctx_with_existing_derived_artefacts.session_id)

    sm = ctx_with_existing_derived_artefacts.sessionmaker
    sid = ctx_with_existing_derived_artefacts.session_id

    # 1. chunks.summary_text réécrits par le nouveau map (pas restés à
    #    l'ancien — preuve que le reset a eu lieu et que le map a re-tourné)
    async with sm() as db:
        chunks = (
            await db.execute(
                select(Chunk).where(Chunk.session_id == sid).order_by(Chunk.ordre)
            )
        ).scalars().all()
    assert chunks[0].summary_text == "nouveau résumé chunk 0"
    assert chunks[1].summary_text == "nouveau résumé chunk 1"

    # 2. Artefacts narrative/elements/pov:* SUPPRIMÉS
    async with sm() as db:
        kinds = (
            await db.execute(
                select(Artifact.kind).where(Artifact.session_id == sid)
            )
        ).scalars().all()
    kinds_set = set(kinds)
    assert "narrative" not in kinds_set
    assert "elements" not in kinds_set
    assert not any(k.startswith("pov:") for k in kinds_set)

    # 3. Nouvel artefact summary présent (UPSERT)
    assert "summary" in kinds_set
    async with sm() as db:
        summary = await db.scalar(
            select(Artifact).where(
                Artifact.session_id == sid, Artifact.kind == "summary"
            )
        )
    assert summary.content_json == {"text": "nouveau résumé global"}
    assert summary.model_used != "old-model"


class _FailingLLM:
    """Map step fails (e.g. DeepInfra 401) — simulates a permanent LLM error."""

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        raise PermanentLLMError("invalid_api_key: User is not authorized")


async def test_failed_regeneration_preserves_existing_data(
    ctx_with_existing_derived_artefacts, monkeypatch
):
    """Story 7.4 — a failed summary regeneration must NOT destroy prior data.

    The destructive cascade now runs only AFTER a successful map+reduce, so a
    permanent LLM error (401) leaves the previous chunk summaries and the
    narrative/elements/pov/summary artifacts untouched. This also means the
    downstream "run POST /artifacts/summary first" guard can never fire as a
    consequence of a failed regen (the chunk summary_text stay non-null).
    """
    _patch_llm(monkeypatch, _FailingLLM())

    from app.jobs.jdr import _generate_summary

    sid = ctx_with_existing_derived_artefacts.session_id

    with pytest.raises(PermanentJobError):
        await _generate_summary(sid)

    sm = ctx_with_existing_derived_artefacts.sessionmaker

    # Chunk summaries are untouched (still the old values, never NULLed).
    async with sm() as db:
        chunks = (
            await db.execute(
                select(Chunk).where(Chunk.session_id == sid).order_by(Chunk.ordre)
            )
        ).scalars().all()
    assert chunks[0].summary_text == "ancien résumé 0"
    assert chunks[1].summary_text == "ancien résumé 1"

    # All derived artifacts + the prior summary survive.
    async with sm() as db:
        rows = (
            await db.execute(
                select(Artifact.kind, Artifact.content_json).where(
                    Artifact.session_id == sid
                )
            )
        ).all()
    kinds = {kind for kind, _ in rows}
    assert "narrative" in kinds
    assert "elements" in kinds
    assert any(k.startswith("pov:") for k in kinds)
    summary_payload = next(c for k, c in rows if k == "summary")
    assert summary_payload == {"text": "ancien résumé global"}
