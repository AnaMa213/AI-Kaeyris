"""US2 / feature 002 — `_generate_summary` job map-reduce.

Le job lit les chunks d'une session non_diarised, appelle le LLM une
fois par chunk (map, persiste `summary_text`), puis une fois de plus
pour consolider (reduce, persiste `Artifact(kind="summary")`). Le
shortcut single-chunk skip le reduce.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
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
    Role,
    Session,
    SessionState,
    TranscriptionMode,
)


@dataclass
class SummaryCtx:
    gm_key_id: UUID
    session_id: UUID
    sessionmaker: async_sessionmaker


class _SequencedStubLLM:
    """Returns responses[i] for the i-th call, captures prompts."""

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


def _patch_llm(monkeypatch, adapter: LLMAdapter) -> None:
    monkeypatch.setattr("app.jobs.jdr.get_llm_adapter", lambda: adapter)


async def _seed_chunks_session(
    db_engine: AsyncEngine,
    monkeypatch,
    *,
    chunk_texts: list[str],
    state: SessionState = SessionState.TRANSCRIBED,
    mode: TranscriptionMode = TranscriptionMode.NON_DIARISED,
) -> SummaryCtx:
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)

    session_id = uuid4()
    gm_id: UUID
    async with sm() as setup:
        gm = ApiKey(
            name=f"gm-summary-{uuid4().hex[:8]}",
            hash=PasswordHasher().hash("noop"),
            role=Role.GM,
            status=ApiKeyStatus.ACTIVE,
        )
        setup.add(gm)
        await setup.flush()
        gm_id = gm.id

        setup.add(
            Session(
                id=session_id,
                title="Summary test",
                recorded_at=datetime.now(UTC),
                gm_key_id=gm_id,
                state=state,
                transcription_mode=mode,
            )
        )
        for i, text in enumerate(chunk_texts):
            setup.add(Chunk(session_id=session_id, ordre=i, text=text))
        await setup.commit()

    return SummaryCtx(gm_key_id=gm_id, session_id=session_id, sessionmaker=sm)


@pytest_asyncio.fixture
async def ctx_3_chunks(db_engine, monkeypatch) -> SummaryCtx:
    return await _seed_chunks_session(
        db_engine,
        monkeypatch,
        chunk_texts=[
            "Première partie de la session.",
            "Deuxième partie.",
            "Troisième et dernière partie.",
        ],
    )


@pytest_asyncio.fixture
async def ctx_1_chunk(db_engine, monkeypatch) -> SummaryCtx:
    return await _seed_chunks_session(
        db_engine,
        monkeypatch,
        chunk_texts=["Une session entière qui tient dans un seul chunk."],
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_generate_summary_3_chunks_calls_map_then_reduce(
    ctx_3_chunks, monkeypatch
):
    """3 chunks → 3 appels map (dans l'ordre) + 1 appel reduce = 4 LLM calls."""
    stub = _SequencedStubLLM(
        responses=[
            "Résumé partiel 1",
            "Résumé partiel 2",
            "Résumé partiel 3",
            "Résumé global consolidé.",
        ]
    )
    _patch_llm(monkeypatch, stub)

    from app.jobs.jdr import _generate_summary

    await _generate_summary(ctx_3_chunks.session_id)

    # Exactement 4 appels LLM (3 map + 1 reduce)
    assert len(stub.calls) == 4

    # Map prompts dans l'ordre ordre ASC
    from app.services.jdr.prompts import (
        SUMMARY_MAP_SYSTEM_PROMPT,
        SUMMARY_REDUCE_SYSTEM_PROMPT,
    )

    for i in range(3):
        assert stub.calls[i]["system"] == SUMMARY_MAP_SYSTEM_PROMPT
    assert stub.calls[3]["system"] == SUMMARY_REDUCE_SYSTEM_PROMPT
    # Le user prompt du map i est le texte du chunk i
    assert "Première" in stub.calls[0]["user"]
    assert "Deuxième" in stub.calls[1]["user"]
    assert "Troisième" in stub.calls[2]["user"]
    # Le reduce contient les 3 résumés partiels dans l'ordre
    reduce_user = stub.calls[3]["user"]
    assert reduce_user.index("partiel 1") < reduce_user.index("partiel 2")
    assert reduce_user.index("partiel 2") < reduce_user.index("partiel 3")

    # Chunks.summary_text peuplés
    async with ctx_3_chunks.sessionmaker() as db:
        chunks = (
            await db.execute(
                select(Chunk)
                .where(Chunk.session_id == ctx_3_chunks.session_id)
                .order_by(Chunk.ordre)
            )
        ).scalars().all()
    summary_texts = [c.summary_text for c in chunks]
    assert summary_texts == [
        "Résumé partiel 1",
        "Résumé partiel 2",
        "Résumé partiel 3",
    ]

    # Artefact summary créé avec le texte reduce
    async with ctx_3_chunks.sessionmaker() as db:
        artifact = await db.scalar(
            select(Artifact).where(
                Artifact.session_id == ctx_3_chunks.session_id,
                Artifact.kind == "summary",
            )
        )
    assert artifact is not None
    assert artifact.content_json == {"text": "Résumé global consolidé."}
    assert artifact.model_used  # non vide


async def test_generate_summary_single_chunk_skips_reduce(
    ctx_1_chunk, monkeypatch
):
    """1 chunk → 1 seul appel LLM (map). Pas de reduce, le summary global
    = le summary_text du chunk unique."""
    stub = _SequencedStubLLM(responses=["Le seul résumé de la session."])
    _patch_llm(monkeypatch, stub)

    from app.jobs.jdr import _generate_summary

    await _generate_summary(ctx_1_chunk.session_id)

    assert len(stub.calls) == 1

    async with ctx_1_chunk.sessionmaker() as db:
        artifact = await db.scalar(
            select(Artifact).where(
                Artifact.session_id == ctx_1_chunk.session_id,
                Artifact.kind == "summary",
            )
        )
    assert artifact is not None
    assert artifact.content_json == {"text": "Le seul résumé de la session."}


# ---------------------------------------------------------------------------
# Validation : refus si conditions non remplies
# ---------------------------------------------------------------------------


async def test_generate_summary_refuses_diarised_session(
    db_engine, monkeypatch
):
    """Mode diarised → PermanentJobError (hors scope sub-jalon 5.5)."""
    ctx = await _seed_chunks_session(
        db_engine,
        monkeypatch,
        chunk_texts=["x"],
        mode=TranscriptionMode.DIARISED,
    )
    _patch_llm(monkeypatch, _SequencedStubLLM(["unused"]))

    from app.jobs import PermanentJobError
    from app.jobs.jdr import _generate_summary

    with pytest.raises(PermanentJobError):
        await _generate_summary(ctx.session_id)


async def test_generate_summary_refuses_no_chunks(db_engine, monkeypatch):
    """Session non_diarised mais aucun chunk → PermanentJobError."""
    ctx = await _seed_chunks_session(db_engine, monkeypatch, chunk_texts=[])
    _patch_llm(monkeypatch, _SequencedStubLLM(["unused"]))

    from app.jobs import PermanentJobError
    from app.jobs.jdr import _generate_summary

    with pytest.raises(PermanentJobError):
        await _generate_summary(ctx.session_id)
