"""US2 — Structured-elements card generation (Lot 4).

Covers:
- ``_parse_elements_response``: tolerance of imperfect LLM output (fenced
  blocks, preamble, missing keys, malformed entries).
- ``_generate_elements``: happy path, error remapping, idempotency, refuses
  on a session that is not yet transcribed.
- HTTP routes: POST/GET JSON + cross-tenant isolation.
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
from app.jobs.jdr import _generate_elements, _parse_elements_response
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
# _parse_elements_response — pure unit tests
# ---------------------------------------------------------------------------


def test_parse_elements_handles_clean_json():
    raw = (
        '{"npcs": [{"name": "Galadriel", "description": "Reine elfe."}], '
        '"locations": [], "items": [], "clues": []}'
    )
    out = _parse_elements_response(raw)
    assert out["npcs"] == [{"name": "Galadriel", "description": "Reine elfe."}]
    assert out["locations"] == []
    assert out["items"] == []
    assert out["clues"] == []


def test_parse_elements_strips_fenced_json_block():
    """Several open models wrap JSON in ```json … ``` despite the prompt."""
    raw = (
        "Voici la fiche :\n"
        "```json\n"
        '{"npcs": [{"name": "Aragorn", "description": "Rôdeur."}], '
        '"locations": [], "items": [], "clues": []}\n'
        "```\n"
    )
    out = _parse_elements_response(raw)
    assert out["npcs"][0]["name"] == "Aragorn"


def test_parse_elements_recovers_from_preamble_around_json():
    raw = (
        "Bien sûr ! Voici l'extraction demandée : "
        '{"npcs": [], "locations": [{"name": "Bree", "description": "Village."}], '
        '"items": [], "clues": []} '
        "J'espère que cela aide."
    )
    out = _parse_elements_response(raw)
    assert out["locations"][0]["name"] == "Bree"


def test_parse_elements_fills_missing_keys_with_empty_lists():
    """Acceptance scenario US 2.3: empty list rather than absent."""
    raw = '{"npcs": [{"name": "Sauron", "description": "Antagoniste."}]}'
    out = _parse_elements_response(raw)
    assert out["npcs"][0]["name"] == "Sauron"
    assert out["locations"] == []
    assert out["items"] == []
    assert out["clues"] == []


def test_parse_elements_returns_four_empty_lists_on_unparseable_input():
    raw = "Désolé, je ne peux pas extraire d'éléments de cette session."
    out = _parse_elements_response(raw)
    assert out == {"npcs": [], "locations": [], "items": [], "clues": []}


def test_parse_elements_drops_entries_without_name():
    raw = (
        '{"npcs": ['
        '{"name": "Frodon", "description": "Hobbit."}, '
        '{"description": "Sans nom — à ignorer."}, '
        '{"name": "", "description": "Vide — à ignorer."}'
        '], "locations": [], "items": [], "clues": []}'
    )
    out = _parse_elements_response(raw)
    assert len(out["npcs"]) == 1
    assert out["npcs"][0]["name"] == "Frodon"


def test_parse_elements_drops_non_dict_entries():
    raw = '{"npcs": ["a string", 42, null], "locations": [], "items": [], "clues": []}'
    out = _parse_elements_response(raw)
    assert out["npcs"] == []


def test_parse_elements_coerces_non_list_field_to_empty():
    raw = '{"npcs": "not a list", "locations": [], "items": [], "clues": []}'
    out = _parse_elements_response(raw)
    assert out["npcs"] == []


def test_parse_elements_defaults_description_to_empty_string():
    raw = '{"npcs": [{"name": "Mystery"}], "locations": [], "items": [], "clues": []}'
    out = _parse_elements_response(raw)
    assert out["npcs"][0]["name"] == "Mystery"
    assert out["npcs"][0]["description"] == ""


# ---------------------------------------------------------------------------
# Job test plumbing
# ---------------------------------------------------------------------------


@dataclass
class ElementsTestContext:
    plain_token: str
    gm_key_id: UUID
    session_id: UUID
    sessionmaker: async_sessionmaker


class _StubLLM:
    """Returns a fixed JSON string. Captures the call args for assertions."""

    def __init__(self, payload: str | None = None) -> None:
        self.payload = payload or (
            '{"npcs": [{"name": "Gandalf", "description": "Magicien gris."}], '
            '"locations": [{"name": "Comté", "description": "Village hobbit."}], '
            '"items": [{"name": "Anneau Unique", "description": "Forgé en Mordor."}], '
            '"clues": [{"name": "Mot de passe Mellon", "description": "Ouvre Moria."}]}'
        )
        self.last_system: str | None = None
        self.last_user: str | None = None

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        self.last_system = system
        self.last_user = user
        return self.payload


class _RaisingLLM:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        raise self._exc


def _patch_llm_adapter(monkeypatch, adapter: LLMAdapter):
    monkeypatch.setattr("app.jobs.jdr.get_llm_adapter", lambda: adapter)


async def _seed_transcribed(
    sm: async_sessionmaker, *, plain_token: str = "gm-elements-token"
) -> ElementsTestContext:
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
                title="Elements test",
                recorded_at=datetime.now(UTC),
                gm_key_id=gm_id,
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
                        "text": "Gandalf nous attend devant les portes de la Moria.",
                    },
                ],
                language="fr",
                model_used="mock:whisper",
                provider="mock",
                completed_at=datetime.now(UTC),
            )
        )
        await setup.commit()
    return ElementsTestContext(
        plain_token=plain_token,
        gm_key_id=gm_id,
        session_id=session_id,
        sessionmaker=sm,
    )


@pytest_asyncio.fixture
async def ctx(db_engine: AsyncEngine, monkeypatch) -> ElementsTestContext:
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)
    return await _seed_transcribed(sm)


@pytest_asyncio.fixture
async def ctx_not_transcribed(
    db_engine: AsyncEngine, monkeypatch
) -> ElementsTestContext:
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)
    plain = "gm-elements-not-ready"
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
                title="Elements not transcribed",
                recorded_at=datetime.now(UTC),
                gm_key_id=gm_id,
                state=SessionState.AUDIO_UPLOADED,
            )
        )
        await setup.commit()
    return ElementsTestContext(
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
# Job
# ---------------------------------------------------------------------------


async def test_generate_elements_writes_artifact_with_four_lists(ctx, monkeypatch):
    stub = _StubLLM()
    _patch_llm_adapter(monkeypatch, stub)

    await _generate_elements(ctx.session_id)

    async with ctx.sessionmaker() as db:
        artifact = await db.scalar(
            select(Artifact).where(
                Artifact.session_id == ctx.session_id,
                Artifact.kind == "elements",
            )
        )
    assert artifact is not None
    content = artifact.content_json
    assert set(content.keys()) == {"npcs", "locations", "items", "clues"}
    assert content["npcs"][0]["name"] == "Gandalf"
    assert content["items"][0]["name"] == "Anneau Unique"


async def test_generate_elements_uses_elements_system_prompt(ctx, monkeypatch):
    """The job must call the LLM with the ELEMENTS_SYSTEM_PROMPT (not the
    narrative one)."""
    stub = _StubLLM()
    _patch_llm_adapter(monkeypatch, stub)

    await _generate_elements(ctx.session_id)

    assert stub.last_system is not None
    # ELEMENTS_SYSTEM_PROMPT mentions the four lists explicitly.
    lowered = stub.last_system.lower()
    assert "npcs" in lowered
    assert "locations" in lowered
    assert "items" in lowered
    assert "clues" in lowered


async def test_generate_elements_handles_empty_lists_per_acceptance_us23(
    ctx, monkeypatch
):
    """US 2.3: empty category -> empty list, not absent key."""
    stub = _StubLLM('{"npcs": [], "locations": [], "items": [], "clues": []}')
    _patch_llm_adapter(monkeypatch, stub)

    await _generate_elements(ctx.session_id)

    async with ctx.sessionmaker() as db:
        artifact = await db.scalar(
            select(Artifact).where(
                Artifact.session_id == ctx.session_id,
                Artifact.kind == "elements",
            )
        )
    assert artifact is not None
    content = artifact.content_json
    assert content["npcs"] == []
    assert content["locations"] == []
    assert content["items"] == []
    assert content["clues"] == []


async def test_generate_elements_is_idempotent(ctx, monkeypatch):
    """A second run overwrites — composite PK (session_id, kind)."""
    first = _StubLLM('{"npcs": [{"name": "First"}], "locations": [], "items": [], "clues": []}')
    _patch_llm_adapter(monkeypatch, first)
    await _generate_elements(ctx.session_id)

    second = _StubLLM('{"npcs": [{"name": "Second"}], "locations": [], "items": [], "clues": []}')
    _patch_llm_adapter(monkeypatch, second)
    await _generate_elements(ctx.session_id)

    async with ctx.sessionmaker() as db:
        rows = (
            await db.scalars(
                select(Artifact).where(
                    Artifact.session_id == ctx.session_id,
                    Artifact.kind == "elements",
                )
            )
        ).all()
    assert len(rows) == 1
    assert rows[0].content_json["npcs"][0]["name"] == "Second"


async def test_generate_elements_remaps_transient_llm_error(ctx, monkeypatch):
    _patch_llm_adapter(monkeypatch, _RaisingLLM(TransientLLMError("upstream 503")))

    with pytest.raises(TransientJobError, match="upstream 503"):
        await _generate_elements(ctx.session_id)


async def test_generate_elements_remaps_permanent_llm_error(ctx, monkeypatch):
    _patch_llm_adapter(monkeypatch, _RaisingLLM(PermanentLLMError("invalid prompt")))

    with pytest.raises(PermanentJobError, match="invalid prompt"):
        await _generate_elements(ctx.session_id)


async def test_generate_elements_refuses_when_not_transcribed(
    ctx_not_transcribed, monkeypatch
):
    _patch_llm_adapter(monkeypatch, _StubLLM())

    with pytest.raises(PermanentJobError, match=r"(?i)not transcribed|transcrib"):
        await _generate_elements(ctx_not_transcribed.session_id)


async def test_generate_elements_unknown_session_raises_permanent(
    db_engine, monkeypatch
):
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)
    _patch_llm_adapter(monkeypatch, _StubLLM())

    with pytest.raises(PermanentJobError, match="not found"):
        await _generate_elements(uuid4())


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


async def test_post_elements_returns_202_with_job_id(
    ctx, make_db_session_dep, monkeypatch
):
    _patch_llm_adapter(monkeypatch, _StubLLM())
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/elements",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["kind"] == "elements"
    assert body["session_id"] == str(ctx.session_id)
    assert body["status"] == "queued"


async def test_post_elements_returns_409_when_not_transcribed(
    ctx_not_transcribed, make_db_session_dep
):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/services/jdr/sessions/{ctx_not_transcribed.session_id}/artifacts/elements",
            headers={"Authorization": f"Bearer {ctx_not_transcribed.plain_token}"},
        )
    assert response.status_code == 409
    assert response.json()["type"].endswith("/session-not-transcribed")


async def test_post_elements_returns_404_for_unknown_session(
    ctx, make_db_session_dep
):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    unknown = "00000000-0000-0000-0000-000000000000"
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/services/jdr/sessions/{unknown}/artifacts/elements",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )
    assert response.status_code == 404


async def test_get_elements_returns_four_named_lists(
    ctx, make_db_session_dep, monkeypatch
):
    _patch_llm_adapter(monkeypatch, _StubLLM())
    await _generate_elements(ctx.session_id)

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/elements",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == str(ctx.session_id)
    assert body["npcs"][0]["name"] == "Gandalf"
    assert body["locations"][0]["name"] == "Comté"
    assert body["items"][0]["name"] == "Anneau Unique"
    assert body["clues"][0]["name"] == "Mot de passe Mellon"
    assert "model_used" in body
    assert "generated_at" in body


async def test_get_elements_returns_404_when_not_generated(
    ctx, make_db_session_dep
):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/elements",
            headers={"Authorization": f"Bearer {ctx.plain_token}"},
        )
    assert response.status_code == 404
    assert response.json()["type"].endswith("/artifact-not-ready")


async def test_get_elements_cross_tenant_returns_404(
    ctx, make_db_session_dep, monkeypatch
):
    """Another GM cannot read GM A's elements card (FR-014)."""
    _patch_llm_adapter(monkeypatch, _StubLLM())
    await _generate_elements(ctx.session_id)

    plain_b = "another-gm-elements-token"
    async with ctx.sessionmaker() as db:
        db.add(
            ApiKey(
                name="another-gm-for-elements",
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
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/elements",
            headers={"Authorization": f"Bearer {plain_b}"},
        )

    assert response.status_code == 404
    # Same leakage discipline as narrative: surfaces session-not-found
    # rather than artifact-not-ready, so the foreign session's existence
    # stays hidden.
    assert response.json()["type"].endswith("/session-not-found")
