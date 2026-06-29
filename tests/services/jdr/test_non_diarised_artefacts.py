"""US3 / feature 002 — narrative/elements/povs sur session non_diarised.

Les jobs existants forkent sur `session.transcription_mode` et, en
mode non_diarised, consomment `chunks.summary_text` (étape map déjà
faite par le job summary) au lieu de la transcription segmentée.
Le format de sortie des artefacts reste identique au mode diarised.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest_asyncio
from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.adapters.llm import LLMAdapter
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    Artifact,
    Chunk,
    Campaign,
    Pj,
    Role,
    Session,
    SessionPlayer,
    SessionState,
    TranscriptionMode,
)


@dataclass
class NDCtx:
    session_id: UUID
    pj_a_id: UUID
    pj_b_id: UUID
    sessionmaker: async_sessionmaker


class _SequencedStubLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        idx = len(self.calls)
        self.calls.append({"system": system, "user": user})
        if idx < len(self._responses):
            return self._responses[idx]
        return self._responses[-1] if self._responses else "{}"


def _patch_llm(monkeypatch, adapter: LLMAdapter) -> None:
    monkeypatch.setattr("app.jobs.jdr.get_llm_adapter", lambda: adapter)


@pytest_asyncio.fixture
async def ctx_non_diarised_with_chunk_summaries(
    db_engine: AsyncEngine, monkeypatch
) -> NDCtx:
    """Seed une session non_diarised avec 2 chunks dont summary_text
    pré-rempli (= comme si _generate_summary avait déjà tourné).
    + 2 PJ déclarés via SessionPlayer.
    """
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)

    session_id = uuid4()
    pj_a_id = uuid4()
    pj_b_id = uuid4()
    gm_id: UUID

    async with sm() as setup:
        gm = ApiKey(
            name=f"gm-nd-{uuid4().hex[:8]}",
            hash=PasswordHasher().hash("noop"),
            role=Role.GM,
            status=ApiKeyStatus.ACTIVE,
        )
        setup.add(gm)
        await setup.flush()
        gm_id = gm.id

        campaign = Campaign(name="ND artefacts campaign", owner_user_id=uuid4())
        setup.add(campaign)
        await setup.flush()

        pj_a = Pj(
            id=pj_a_id,
            name="Aragorn",
            owner_gm_key_id=gm_id,
            campaign_id=campaign.id,
        )
        pj_b = Pj(
            id=pj_b_id,
            name="Galadriel",
            owner_gm_key_id=gm_id,
            campaign_id=campaign.id,
        )
        setup.add(pj_a)
        setup.add(pj_b)

        setup.add(
            Session(
                id=session_id,
                title="ND artefacts test",
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
                text="texte original 0",
                summary_text="résumé partiel chunk 0",
            )
        )
        setup.add(
            Chunk(
                session_id=session_id,
                ordre=1,
                text="texte original 1",
                summary_text="résumé partiel chunk 1",
            )
        )
        setup.add(SessionPlayer(session_id=session_id, pj_id=pj_a_id))
        setup.add(SessionPlayer(session_id=session_id, pj_id=pj_b_id))
        await setup.commit()

    return NDCtx(
        session_id=session_id,
        pj_a_id=pj_a_id,
        pj_b_id=pj_b_id,
        sessionmaker=sm,
    )


# ---------------------------------------------------------------------------
# narrative en mode non_diarised
# ---------------------------------------------------------------------------


async def test_narrative_non_diarised_consumes_chunk_summaries(
    ctx_non_diarised_with_chunk_summaries, monkeypatch
):
    stub = _SequencedStubLLM(responses=["Récit narratif global."])
    _patch_llm(monkeypatch, stub)

    from app.jobs.jdr import _generate_narrative

    await _generate_narrative(ctx_non_diarised_with_chunk_summaries.session_id)

    assert len(stub.calls) == 1
    user_prompt = stub.calls[0]["user"]
    # Le user prompt contient les résumés partiels des chunks, pas la
    # transcription brute (segments)
    assert "résumé partiel chunk 0" in user_prompt
    assert "résumé partiel chunk 1" in user_prompt
    assert "texte original 0" not in user_prompt

    # Artefact narrative créé
    async with ctx_non_diarised_with_chunk_summaries.sessionmaker() as db:
        narr = await db.scalar(
            select(Artifact).where(
                Artifact.session_id
                == ctx_non_diarised_with_chunk_summaries.session_id,
                Artifact.kind == "narrative",
            )
        )
    assert narr is not None
    assert narr.content_json == {"text": "Récit narratif global."}


# ---------------------------------------------------------------------------
# elements en mode non_diarised
# ---------------------------------------------------------------------------


async def test_elements_non_diarised_consumes_chunk_summaries(
    ctx_non_diarised_with_chunk_summaries, monkeypatch
):
    stub = _SequencedStubLLM(
        responses=[
            '{"npcs": [{"name": "Marchand", "description": "PNJ"}], '
            '"locations": [], "items": [], "clues": []}'
        ]
    )
    _patch_llm(monkeypatch, stub)

    from app.jobs.jdr import _generate_elements

    await _generate_elements(ctx_non_diarised_with_chunk_summaries.session_id)

    assert len(stub.calls) == 1
    user_prompt = stub.calls[0]["user"]
    assert "résumé partiel chunk 0" in user_prompt
    assert "résumé partiel chunk 1" in user_prompt

    async with ctx_non_diarised_with_chunk_summaries.sessionmaker() as db:
        elem = await db.scalar(
            select(Artifact).where(
                Artifact.session_id
                == ctx_non_diarised_with_chunk_summaries.session_id,
                Artifact.kind == "elements",
            )
        )
    assert elem is not None
    # BD-26: buckets are flattened into a category-tagged list (npcs -> PNJ).
    assert elem.content_json["elements"] == [
        {"category": "PNJ", "name": "Marchand", "description": "PNJ"}
    ]


# ---------------------------------------------------------------------------
# povs en mode non_diarised : lit SessionPlayer (pas SessionPjMapping)
# ---------------------------------------------------------------------------


async def test_povs_non_diarised_uses_session_players(
    ctx_non_diarised_with_chunk_summaries, monkeypatch
):
    """Avec 2 PJ déclarés via /players, 2 rows pov:<pj_id> sont produites
    chacune à partir des chunks.summary_text."""
    stub = _SequencedStubLLM(
        responses=["POV d'Aragorn.", "POV de Galadriel."]
    )
    _patch_llm(monkeypatch, stub)

    from app.jobs.jdr import _generate_povs

    await _generate_povs(ctx_non_diarised_with_chunk_summaries.session_id)

    # 2 appels LLM (un par PJ), pas 4 (pas de re-map)
    assert len(stub.calls) == 2

    # Chaque user prompt contient les résumés partiels concaténés
    for call in stub.calls:
        assert "résumé partiel chunk 0" in call["user"]
        assert "résumé partiel chunk 1" in call["user"]

    # Les 2 user prompts mentionnent les noms des PJ scoppés
    all_user_prompts = " ".join(c["user"] for c in stub.calls)
    assert "Aragorn" in all_user_prompts
    assert "Galadriel" in all_user_prompts

    # 2 artefacts pov:<pj_id> créés
    async with ctx_non_diarised_with_chunk_summaries.sessionmaker() as db:
        povs = (
            await db.execute(
                select(Artifact).where(
                    Artifact.session_id
                    == ctx_non_diarised_with_chunk_summaries.session_id,
                    Artifact.kind.like("pov:%"),
                )
            )
        ).scalars().all()
    pov_kinds = sorted(p.kind for p in povs)
    assert pov_kinds == sorted(
        [
            f"pov:{ctx_non_diarised_with_chunk_summaries.pj_a_id}",
            f"pov:{ctx_non_diarised_with_chunk_summaries.pj_b_id}",
        ]
    )
