"""Epic 8 / US1 (BD-23) — synchronous MJ edits of text artefacts.

PATCH .../artifacts/{summary,narrative} and .../artifacts/povs/{pj_id} replace
the artefact text in a single synchronous write (no job), set manual-edit
provenance (is_edited/edited_at), and leave the last-generation record
(model_used/generated_at) untouched. MJ-only; the artefact must already exist.
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
from redis import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.redis_client import get_redis
from app.jobs import enqueue_job, get_default_queue
from app.jobs.jdr import generate_summary_job, transcribe_session_job
from app.services.jdr.db.models import (
    ApiKey,
    ApiKeyStatus,
    Artifact,
    Campaign,
    Job,
    JobKind,
    JobStatus,
    Pj,
    Role,
    Session,
    SessionState,
    TranscriptionMode,
)
from app.services.jdr.router import router as jdr_router

# A fixed generation timestamp so we can assert an edit never moves it.
GENERATED_AT = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
GEN_MODEL = "mock:llm-v1"


@dataclass
class EditTestContext:
    plain_token: str
    gm_key_id: UUID
    pj_id: UUID
    session_id: UUID
    sessionmaker: async_sessionmaker


async def _seed_session_with_artifacts(
    sm: async_sessionmaker,
    *,
    plain_token: str = "gm-edit-token",
) -> EditTestContext:
    """GM + Campaign + PJ + transcribed Session + generated summary/narrative/pov."""
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

        campaign = Campaign(name="Edit campaign", owner_user_id=uuid4())
        setup.add(campaign)
        await setup.flush()

        pj = Pj(name="Aragorn", owner_gm_key_id=gm_id, campaign_id=campaign.id)
        setup.add(pj)
        await setup.flush()
        pj_id = pj.id

        setup.add(
            Session(
                id=session_id,
                title="Edit test session",
                recorded_at=datetime.now(UTC),
                gm_key_id=gm_id,
                campaign_id=campaign.id,
                state=SessionState.TRANSCRIBED,
                transcription_mode=TranscriptionMode.NON_DIARISED,
            )
        )
        for kind, text in (
            ("summary", "Résumé généré par l'IA."),
            ("narrative", "Récit généré par l'IA."),
            (f"pov:{pj_id}", "POV généré par l'IA."),
        ):
            setup.add(
                Artifact(
                    session_id=session_id,
                    kind=kind,
                    content_json={"text": text},
                    model_used=GEN_MODEL,
                    generated_at=GENERATED_AT,
                )
            )
        setup.add(
            Artifact(
                session_id=session_id,
                kind="elements",
                content_json={
                    "elements": [
                        {
                            "category": "PNJ",
                            "name": "Gandalf",
                            "description": "Initial element.",
                        }
                    ]
                },
                model_used=GEN_MODEL,
                generated_at=GENERATED_AT,
            )
        )
        await setup.commit()

    return EditTestContext(
        plain_token=plain_token,
        gm_key_id=gm_id,
        pj_id=pj_id,
        session_id=session_id,
        sessionmaker=sm,
    )


@pytest_asyncio.fixture
async def ctx(db_engine: AsyncEngine) -> EditTestContext:
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    return await _seed_session_with_artifacts(sm)


def _make_jdr_app(
    make_db_session_dep: Callable[..., Any],
    redis_client: Redis | None = None,
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(jdr_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = (
        lambda: redis_client or fakeredis.FakeStrictRedis()
    )
    return app


def _auth(ctx: EditTestContext) -> dict[str, str]:
    return {"Authorization": f"Bearer {ctx.plain_token}"}


async def _attach_current_rq_job(
    ctx: EditTestContext,
    *,
    redis_client: Redis,
    func: Callable[..., Any],
    kind: JobKind,
) -> str:
    queue = get_default_queue(redis_client)
    rq_job = enqueue_job(queue, func, ctx.session_id)
    async with ctx.sessionmaker() as db:
        session = await db.get(Session, ctx.session_id)
        db.add(
            Job(
                id=rq_job.id,
                kind=kind,
                session_id=ctx.session_id,
                status=JobStatus.QUEUED,
                queued_at=datetime.now(UTC),
            )
        )
        await db.flush()
        session.current_job_id = rq_job.id
        await db.commit()
    return rq_job.id


async def _artifact_snapshot(
    ctx: EditTestContext, kind: str
) -> tuple[dict[str, Any], bool, datetime | None, str | None]:
    async with ctx.sessionmaker() as db:
        artifact = await db.get(Artifact, (ctx.session_id, kind))
        assert artifact is not None
        return (
            artifact.content_json,
            artifact.is_edited,
            artifact.edited_at,
            artifact.edited_by,
        )


# ---------------------------------------------------------------------------
# Happy path: round-trip + provenance + immutability of generation record
# ---------------------------------------------------------------------------


async def test_patch_summary_round_trip_and_provenance(ctx, make_db_session_dep):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    new_text = "# Résumé corrigé à la main\n\nNouvelle version."

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        patch = await client.patch(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/summary",
            headers=_auth(ctx),
            json={"text": new_text},
        )
        get = await client.get(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/summary",
            headers=_auth(ctx),
        )

    assert patch.status_code == 200
    body = patch.json()
    assert body["text"] == new_text
    assert body["is_edited"] is True
    assert body["edited_at"] is not None
    # The last-generation record is untouched by an edit (FR-006).
    assert body["model_used"] == GEN_MODEL
    assert body["generated_at"] == GENERATED_AT.isoformat()
    # GET reflects the edited text and provenance.
    assert get.json()["text"] == new_text
    assert get.json()["is_edited"] is True


async def test_patch_narrative_round_trip(ctx, make_db_session_dep):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/narrative",
            headers=_auth(ctx),
            json={"text": "Récit corrigé."},
        )
    assert resp.status_code == 200
    assert resp.json()["text"] == "Récit corrigé."
    assert resp.json()["is_edited"] is True


async def test_patch_pov_round_trip(ctx, make_db_session_dep):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/povs/{ctx.pj_id}",
            headers=_auth(ctx),
            json={"text": "POV corrigé pour ce PJ."},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "POV corrigé pour ce PJ."
    assert body["pj_id"] == str(ctx.pj_id)
    assert body["is_edited"] is True


# ---------------------------------------------------------------------------
# Guards: artefact absent (404), blank body (422), cross-tenant (404)
# ---------------------------------------------------------------------------


async def test_patch_summary_404_when_artifact_absent(ctx, make_db_session_dep):
    # Delete the summary so the edit targets a non-existent artefact.
    async with ctx.sessionmaker() as db:
        artifact = await db.get(Artifact, (ctx.session_id, "summary"))
        await db.delete(artifact)
        await db.commit()

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/summary",
            headers=_auth(ctx),
            json={"text": "peu importe"},
        )
    assert resp.status_code == 404
    assert resp.json()["type"].endswith("/artifact-not-ready")


async def test_patch_summary_422_when_blank(ctx, make_db_session_dep):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/summary",
            headers=_auth(ctx),
            json={"text": "   "},
        )
    assert resp.status_code == 422


async def test_patch_summary_cross_tenant_returns_404(ctx, make_db_session_dep):
    plain_b = "another-gm-token"
    async with ctx.sessionmaker() as db:
        db.add(
            ApiKey(
                name="another-gm-for-edit",
                hash=PasswordHasher().hash(plain_b),
                role=Role.GM,
                status=ApiKeyStatus.ACTIVE,
            )
        )
        await db.commit()

    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/summary",
            headers={"Authorization": f"Bearer {plain_b}"},
            json={"text": "tentative d'édition"},
        )
    assert resp.status_code == 404


@pytest.mark.parametrize(
    ("method", "path_builder", "payload", "artifact_kind"),
    [
        (
            "patch",
            lambda ctx: f"/services/jdr/sessions/{ctx.session_id}/artifacts/summary",
            {"text": "Manual summary update."},
            "summary",
        ),
        (
            "patch",
            lambda ctx: f"/services/jdr/sessions/{ctx.session_id}/artifacts/narrative",
            {"text": "Manual narrative update."},
            "narrative",
        ),
        (
            "patch",
            lambda ctx: (
                f"/services/jdr/sessions/{ctx.session_id}/artifacts/povs/{ctx.pj_id}"
            ),
            {"text": "Manual POV update."},
            "pov",
        ),
        (
            "put",
            lambda ctx: f"/services/jdr/sessions/{ctx.session_id}/artifacts/elements",
            {
                "elements": [
                    {
                        "category": "PNJ",
                        "name": "Bilbo",
                        "description": "Replacement element.",
                    }
                ]
            },
            "elements",
        ),
    ],
)
async def test_manual_artifact_edits_reject_while_artifact_job_is_active(
    ctx,
    make_db_session_dep,
    method,
    path_builder,
    payload,
    artifact_kind,
):
    redis_client = fakeredis.FakeStrictRedis()
    await _attach_current_rq_job(
        ctx,
        redis_client=redis_client,
        func=generate_summary_job,
        kind=JobKind.SUMMARY,
    )
    kind = f"pov:{ctx.pj_id}" if artifact_kind == "pov" else artifact_kind
    before = await _artifact_snapshot(ctx, kind)

    app = _make_jdr_app(make_db_session_dep, redis_client)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await getattr(client, method)(
            path_builder(ctx),
            headers=_auth(ctx),
            json=payload,
        )

    assert resp.status_code == 409
    assert resp.json()["type"].endswith("/artifact-busy")
    assert await _artifact_snapshot(ctx, kind) == before


async def test_non_artifact_active_job_does_not_block_artifact_edit(
    ctx, make_db_session_dep
):
    redis_client = fakeredis.FakeStrictRedis()
    await _attach_current_rq_job(
        ctx,
        redis_client=redis_client,
        func=transcribe_session_job,
        kind=JobKind.TRANSCRIPTION,
    )

    app = _make_jdr_app(make_db_session_dep, redis_client)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            f"/services/jdr/sessions/{ctx.session_id}/artifacts/summary",
            headers=_auth(ctx),
            json={"text": "Manual edit despite transcription job."},
        )

    assert resp.status_code == 200
    assert resp.json()["text"] == "Manual edit despite transcription job."
