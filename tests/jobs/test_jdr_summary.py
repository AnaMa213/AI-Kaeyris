"""US2 / feature 002 — `_generate_summary` job map-reduce.

Le job lit les chunks d'une session non_diarised, appelle le LLM une
fois par chunk (map, persiste `summary_text`), puis une fois de plus
pour consolider (reduce, persiste `Artifact(kind="summary")`). Le
shortcut single-chunk skip le reduce.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.adapters.llm import LLMAdapter, TransientLLMError
from app.jobs import TransientJobError
from app.adapters.transcription import (
    TranscriptionResult,
    TranscriptionSegment,
)
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    Artifact,
    AudioSource,
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


class _RaisingStubLLM:
    """Raises the configured error on every LLM call."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        raise self._exc


def _patch_llm(monkeypatch, adapter: LLMAdapter) -> None:
    monkeypatch.setattr("app.jobs.jdr.get_llm_adapter", lambda: adapter)


async def _seed_chunks_session(
    db_engine: AsyncEngine,
    monkeypatch,
    *,
    chunk_texts: list[str],
    state: SessionState = SessionState.TRANSCRIBED,
    mode: TranscriptionMode = TranscriptionMode.NON_DIARISED,
    edited_transcript_md: str | None = None,
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
                edited_transcript_md=edited_transcript_md,
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


async def test_generate_summary_uses_edited_markdown_source(
    db_engine, monkeypatch
):
    ctx = await _seed_chunks_session(
        db_engine,
        monkeypatch,
        chunk_texts=["Texte automatique qui ne doit pas être envoyé."],
        edited_transcript_md="## Correction\n\nTexte corrigé distinctif.",
    )
    async with ctx.sessionmaker() as db:
        chunk = await db.scalar(
            select(Chunk).where(Chunk.session_id == ctx.session_id)
        )
        assert chunk is not None
        chunk.summary_text = "Résumé périmé du chunk automatique."
        await db.commit()

    stub = _SequencedStubLLM(responses=["Résumé du texte corrigé."])
    _patch_llm(monkeypatch, stub)

    from app.jobs.jdr import _generate_summary

    await _generate_summary(ctx.session_id)

    assert len(stub.calls) == 1
    assert "Texte corrigé distinctif" in stub.calls[0]["user"]
    assert "Texte automatique" not in stub.calls[0]["user"]

    async with ctx.sessionmaker() as db:
        chunk = await db.scalar(
            select(Chunk).where(Chunk.session_id == ctx.session_id)
        )
        artifact = await db.scalar(
            select(Artifact).where(
                Artifact.session_id == ctx.session_id,
                Artifact.kind == "summary",
            )
        )
    assert chunk is not None
    assert chunk.text == "Texte automatique qui ne doit pas être envoyé."
    assert chunk.summary_text is None
    assert artifact is not None
    assert artifact.content_json == {"text": "Résumé du texte corrigé."}


async def test_generate_summary_falls_back_to_automatic_chunks_without_edit(
    ctx_1_chunk, monkeypatch
):
    stub = _SequencedStubLLM(responses=["Résumé depuis le chunk auto."])
    _patch_llm(monkeypatch, stub)

    from app.jobs.jdr import _generate_summary

    await _generate_summary(ctx_1_chunk.session_id)

    assert len(stub.calls) == 1
    assert "Une session entière" in stub.calls[0]["user"]


async def test_generation_source_helper_prefers_edited_markdown(
    db_engine, monkeypatch
):
    ctx = await _seed_chunks_session(
        db_engine,
        monkeypatch,
        chunk_texts=["Chunk automatique sans summary_text."],
        edited_transcript_md="Texte édité pour les dérivés.",
    )

    from app.jobs.jdr import _load_session_source_document

    source_text, _ = await _load_session_source_document(
        ctx.session_id, artefact_label="narrative"
    )

    assert source_text == "Texte édité pour les dérivés."


async def test_generation_source_helper_uses_latest_edited_markdown(
    db_engine, monkeypatch
):
    ctx = await _seed_chunks_session(
        db_engine,
        monkeypatch,
        chunk_texts=["Chunk automatique."],
        edited_transcript_md="Première version corrigée.",
    )

    from app.jobs.jdr import _load_session_source_document

    source_text, _ = await _load_session_source_document(
        ctx.session_id, artefact_label="elements"
    )
    assert source_text == "Première version corrigée."

    async with ctx.sessionmaker() as db:
        session_row = await db.scalar(
            select(Session).where(Session.id == ctx.session_id)
        )
        assert session_row is not None
        session_row.edited_transcript_md = "Deuxième version corrigée."
        await db.commit()

    source_text, _ = await _load_session_source_document(
        ctx.session_id, artefact_label="elements"
    )
    assert source_text == "Deuxième version corrigée."


# ---------------------------------------------------------------------------
# BD-11 - LLM connectivity failure mapping
# ---------------------------------------------------------------------------


async def test_generate_summary_remaps_transient_llm_error(
    ctx_1_chunk, monkeypatch
):
    _patch_llm(
        monkeypatch,
        _RaisingStubLLM(TransientLLMError("APIConnectionError: Connection error.")),
    )

    from app.jobs.jdr import _generate_summary

    with pytest.raises(TransientJobError, match="APIConnectionError"):
        await _generate_summary(ctx_1_chunk.session_id)


async def test_generate_summary_failure_does_not_overwrite_existing_summary(
    ctx_1_chunk, monkeypatch
):
    async with ctx_1_chunk.sessionmaker() as db:
        db.add(
            Artifact(
                session_id=ctx_1_chunk.session_id,
                kind="summary",
                content_json={"text": "ancien resume global"},
                model_used="old-model",
            )
        )
        await db.commit()

    _patch_llm(
        monkeypatch,
        _RaisingStubLLM(TransientLLMError("APIConnectionError: Connection error.")),
    )

    from app.jobs.jdr import _generate_summary

    with pytest.raises(TransientJobError):
        await _generate_summary(ctx_1_chunk.session_id)

    async with ctx_1_chunk.sessionmaker() as db:
        artifact = await db.scalar(
            select(Artifact).where(
                Artifact.session_id == ctx_1_chunk.session_id,
                Artifact.kind == "summary",
            )
        )

    assert artifact is not None
    assert artifact.content_json == {"text": "ancien resume global"}
    assert artifact.model_used == "old-model"


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


# ---------------------------------------------------------------------------
# BD-10 — chunked transcription progress callback (US1)
# ---------------------------------------------------------------------------


class _StubTranscriber:
    """Returns a one-segment result per chunk; ignores the audio path."""

    async def transcribe(
        self, *, audio_path: str, language_hint: str | None = None
    ) -> TranscriptionResult:
        return TranscriptionResult(
            segments=[
                TranscriptionSegment(
                    speaker_label="speaker_1",
                    start_seconds=0.0,
                    end_seconds=1.0,
                    text="bonjour",
                )
            ],
            language="fr",
            model_used="stub",
            provider="mock",
        )


async def test_chunked_transcription_progress_is_monotone(monkeypatch):
    """``_transcribe_with_optional_chunking`` invokes ``on_progress`` once per
    completed chunk with a monotone ``(chunks_done, chunks_total)`` shaped
    around ``1 <= done <= total`` — the denominator the route turns into a
    real percent. ffmpeg is mocked out so the test stays a pure unit test."""

    @contextmanager
    def _fake_chunked_audio(source, chunk_duration_seconds, work_dir):
        yield [
            (0.0, Path("chunk_0.wav")),
            (30.0, Path("chunk_1.wav")),
            (60.0, Path("chunk_2.wav")),
        ]

    monkeypatch.setattr("app.jobs.jdr.chunked_audio", _fake_chunked_audio)

    from app.jobs.jdr import _transcribe_with_optional_chunking

    events: list[tuple[int, int]] = []
    result = await _transcribe_with_optional_chunking(
        adapter=_StubTranscriber(),
        audio_path=Path("session.wav"),
        session_id=uuid4(),
        language_hint=None,
        chunk_duration_seconds=30,
        on_progress=lambda done, total: events.append((done, total)),
    )

    # One event per chunk, in order, with a stable denominator.
    assert [done for done, _ in events] == [1, 2, 3]
    assert all(total == 3 for _, total in events)
    assert all(1 <= done <= total for done, total in events)
    # The helper still stitches every chunk's segments together.
    assert len(result.segments) == 3


async def test_chunked_transcription_without_callback_unchanged(monkeypatch):
    """No callback → existing behaviour (back-compat: the chunk helper must
    not require ``on_progress``)."""

    @contextmanager
    def _fake_chunked_audio(source, chunk_duration_seconds, work_dir):
        yield [(0.0, Path("chunk_0.wav")), (30.0, Path("chunk_1.wav"))]

    monkeypatch.setattr("app.jobs.jdr.chunked_audio", _fake_chunked_audio)

    from app.jobs.jdr import _transcribe_with_optional_chunking

    result = await _transcribe_with_optional_chunking(
        adapter=_StubTranscriber(),
        audio_path=Path("session.wav"),
        session_id=uuid4(),
        language_hint=None,
        chunk_duration_seconds=30,
    )
    assert len(result.segments) == 2


# ---------------------------------------------------------------------------
# BD-10 — failure progress emission (US3)
# ---------------------------------------------------------------------------


async def _seed_audio_session(db_engine, monkeypatch, tmp_path) -> SummaryCtx:
    """Seed a session ready for transcription (AUDIO_UPLOADED + audio file).

    Single-shot transcription (chunking disabled) so the adapter is called
    once with the raw path; the test adapter raises without reading it.
    """
    monkeypatch.setattr(
        "app.core.config.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    monkeypatch.setattr(
        "app.jobs.jdr.settings.KAEYRIS_DATA_DIR", str(tmp_path)
    )
    monkeypatch.setattr(
        "app.jobs.jdr.settings.TRANSCRIPTION_CHUNK_DURATION_SECONDS", 0
    )
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)

    session_id = uuid4()
    async with sm() as setup:
        gm = ApiKey(
            name=f"gm-failprog-{uuid4().hex[:8]}",
            hash=PasswordHasher().hash("noop"),
            role=Role.GM,
            status=ApiKeyStatus.ACTIVE,
        )
        setup.add(gm)
        await setup.flush()
        setup.add(
            Session(
                id=session_id,
                title="Failure progress test",
                recorded_at=datetime.now(UTC),
                gm_key_id=gm.id,
                state=SessionState.AUDIO_UPLOADED,
            )
        )
        setup.add(
            AudioSource(
                session_id=session_id,
                path=f"audios/{session_id}.m4a",
                sha256="a" * 64,
                size_bytes=14,
                duration_seconds=10,
            )
        )
        gm_id = gm.id
        await setup.commit()

    audio_dir = tmp_path / "audios"
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / f"{session_id}.m4a").write_bytes(b"fake-m4a-bytes")

    return SummaryCtx(gm_key_id=gm_id, session_id=session_id, sessionmaker=sm)


async def test_transcribe_failure_emits_failed_without_resetting_percent(
    db_engine, monkeypatch, tmp_path
):
    """When transcription fails, the worker emits phase=failed *without* a
    percent — so the last known ``progress_percent`` on the job metadata is
    preserved rather than reset to 0 (BD-10 US3)."""
    from app.adapters.transcription import PermanentTranscriptionError

    ctx = await _seed_audio_session(db_engine, monkeypatch, tmp_path)

    class _RaisingTranscriber:
        async def transcribe(self, *, audio_path, language_hint=None):
            raise PermanentTranscriptionError("bad audio")

    monkeypatch.setattr(
        "app.jobs.jdr.get_transcription_adapter", lambda: _RaisingTranscriber()
    )

    from app.jobs import PermanentJobError
    from app.jobs.jdr import _transcribe_session

    events: list[tuple[str, int | None]] = []

    def _recorder(phase: str, percent: int | None = None) -> None:
        events.append((phase, percent))

    with pytest.raises(PermanentJobError):
        await _transcribe_session(ctx.session_id, report_progress=_recorder)

    # Worker announced work, then a terminal failure carrying no percent
    # (None => the real reporter keeps the previously stored value).
    assert ("failed", None) in events
    assert events[-1] == ("failed", None)


def test_progress_reporter_failed_preserves_last_percent_on_meta():
    """Unit check of the reporter contract: emitting ``failed`` with no
    percent must keep the last ``progress_percent`` already on the job."""
    import fakeredis

    from app.jobs import enqueue_job, get_default_queue
    from app.jobs.jdr import _ProgressReporter, transcribe_session_job
    from rq.job import Job

    redis_client = fakeredis.FakeStrictRedis()
    queue = get_default_queue(redis_client)
    job = enqueue_job(queue, transcribe_session_job, uuid4())

    reporter = _ProgressReporter(job)
    reporter("transcribing", 50)
    reporter("failed")  # no percent -> preserve 50

    fresh = Job.fetch(job.id, connection=redis_client)
    assert fresh.meta["phase"] == "failed"
    assert fresh.meta["progress_percent"] == 50


def test_progress_reporter_noop_without_job():
    """Outside a worker (get_current_job() is None) the reporter is a safe
    no-op so the transcription core stays queue-agnostic."""
    from app.jobs.jdr import _ProgressReporter

    _ProgressReporter(None)("transcribing", 10)  # must not raise
