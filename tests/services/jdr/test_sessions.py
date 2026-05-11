"""US1 — Sessions CRUD endpoints.

Tests are written before the implementation per ADR 0006 testing
discipline. Each scenario corresponds to an acceptance criterion of
``specs/001-kaeyris-jdr/spec.md`` US1.
"""

from collections.abc import Callable
from datetime import UTC, datetime

import fakeredis
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.redis_client import get_redis
from app.services.jdr.db.models import ApiKey, ApiKeyStatus, Role
from app.services.jdr.router import router as jdr_router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seeded_gm(db_session: AsyncSession) -> tuple[str, ApiKey]:
    """Insert one active GM key. Returns (plaintext_token, row)."""
    plain = "gm-sessions-token-do-not-use"
    api_key = ApiKey(
        name="gm-sessions-test",
        hash=PasswordHasher().hash(plain),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
        pj_id=None,
    )
    db_session.add(api_key)
    await db_session.commit()
    await db_session.refresh(api_key)
    return plain, api_key


@pytest_asyncio.fixture
async def another_seeded_gm(db_session: AsyncSession) -> tuple[str, ApiKey]:
    """A second GM (independent ownership) for cross-tenant isolation tests."""
    plain = "other-gm-token"
    api_key = ApiKey(
        name="other-gm",
        hash=PasswordHasher().hash(plain),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
        pj_id=None,
    )
    db_session.add(api_key)
    await db_session.commit()
    await db_session.refresh(api_key)
    return plain, api_key


def _make_jdr_app(
    make_db_session_dep: Callable[..., object],
    redis_client: Redis,
) -> FastAPI:
    """A mini app that mounts only the JDR router with the test deps."""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(jdr_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = lambda: redis_client
    return app


# ---------------------------------------------------------------------------
# POST /services/jdr/sessions
# ---------------------------------------------------------------------------


async def test_create_session_returns_201_with_id(
    seeded_gm, make_db_session_dep
):
    plain, _ = seeded_gm
    app = _make_jdr_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)
    payload = {
        "title": "Donjon des morts-vivants — chapitre 4",
        "recorded_at": "2026-05-04T20:30:00+00:00",
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/services/jdr/sessions",
            json=payload,
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == payload["title"]
    assert body["state"] == "created"
    assert body["mode"] == "batch"
    assert "id" in body and isinstance(body["id"], str)
    assert "created_at" in body


async def test_create_session_requires_authentication(make_db_session_dep):
    app = _make_jdr_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/services/jdr/sessions",
            json={"title": "x", "recorded_at": "2026-05-04T20:30:00+00:00"},
        )

    assert response.status_code == 401


async def test_create_session_rejects_player_role(
    db_session, make_db_session_dep
):
    """Only GMs may create sessions."""
    plain = "player-cannot-create-token"
    # The player needs a pj_id, so we insert a PJ first.
    from app.services.jdr.db.models import Pj

    gm = ApiKey(
        name="gm-owner-of-pj",
        hash=PasswordHasher().hash("gm-token"),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
    )
    db_session.add(gm)
    await db_session.flush()
    pj = Pj(name="Aragorn", owner_gm_key_id=gm.id)
    db_session.add(pj)
    await db_session.flush()
    db_session.add(
        ApiKey(
            name="player-aragorn",
            hash=PasswordHasher().hash(plain),
            role=Role.PLAYER,
            status=ApiKeyStatus.ACTIVE,
            pj_id=pj.id,
        )
    )
    await db_session.commit()

    app = _make_jdr_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/services/jdr/sessions",
            json={"title": "x", "recorded_at": "2026-05-04T20:30:00+00:00"},
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 403


async def test_create_session_validates_payload(seeded_gm, make_db_session_dep):
    plain, _ = seeded_gm
    app = _make_jdr_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Missing required fields
        response = await client.post(
            "/services/jdr/sessions",
            json={"title": ""},  # blank title + missing recorded_at
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"


# ---------------------------------------------------------------------------
# GET /services/jdr/sessions
# ---------------------------------------------------------------------------


async def test_list_sessions_returns_only_current_gm_sessions(
    seeded_gm, another_seeded_gm, make_db_session_dep
):
    plain_a, _ = seeded_gm
    plain_b, _ = another_seeded_gm
    app = _make_jdr_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # GM A creates two sessions
        for title in ["Session A1", "Session A2"]:
            await client.post(
                "/services/jdr/sessions",
                json={"title": title, "recorded_at": "2026-05-04T20:30:00+00:00"},
                headers={"Authorization": f"Bearer {plain_a}"},
            )
        # GM B creates one session
        await client.post(
            "/services/jdr/sessions",
            json={"title": "Session B1", "recorded_at": "2026-05-04T20:30:00+00:00"},
            headers={"Authorization": f"Bearer {plain_b}"},
        )

        # GM A only sees its two
        list_a = await client.get(
            "/services/jdr/sessions",
            headers={"Authorization": f"Bearer {plain_a}"},
        )
        # GM B only sees its one
        list_b = await client.get(
            "/services/jdr/sessions",
            headers={"Authorization": f"Bearer {plain_b}"},
        )

    assert list_a.status_code == 200
    items_a = list_a.json()["items"]
    titles_a = sorted(s["title"] for s in items_a)
    assert titles_a == ["Session A1", "Session A2"]

    assert list_b.status_code == 200
    items_b = list_b.json()["items"]
    assert [s["title"] for s in items_b] == ["Session B1"]


# ---------------------------------------------------------------------------
# GET /services/jdr/sessions/{id}
# ---------------------------------------------------------------------------


async def test_get_session_by_id(seeded_gm, make_db_session_dep):
    plain, _ = seeded_gm
    app = _make_jdr_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create = await client.post(
            "/services/jdr/sessions",
            json={"title": "Lookup test", "recorded_at": "2026-05-04T20:30:00+00:00"},
            headers={"Authorization": f"Bearer {plain}"},
        )
        session_id = create.json()["id"]

        get_resp = await client.get(
            f"/services/jdr/sessions/{session_id}",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == session_id
    assert get_resp.json()["title"] == "Lookup test"


async def test_get_session_belonging_to_another_gm_returns_404(
    seeded_gm, another_seeded_gm, make_db_session_dep
):
    """Cross-tenant isolation: GM B's session is invisible to GM A."""
    plain_a, _ = seeded_gm
    plain_b, _ = another_seeded_gm
    app = _make_jdr_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create_b = await client.post(
            "/services/jdr/sessions",
            json={
                "title": "GM B's session",
                "recorded_at": "2026-05-04T20:30:00+00:00",
            },
            headers={"Authorization": f"Bearer {plain_b}"},
        )
        session_id = create_b.json()["id"]

        # GM A asks for GM B's session
        response = await client.get(
            f"/services/jdr/sessions/{session_id}",
            headers={"Authorization": f"Bearer {plain_a}"},
        )

    # 404 not 403 — leaks less information about what exists
    assert response.status_code == 404


async def test_get_session_with_unknown_id_returns_404(
    seeded_gm, make_db_session_dep
):
    plain, _ = seeded_gm
    app = _make_jdr_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)
    unknown_uuid = "00000000-0000-0000-0000-000000000000"

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/services/jdr/sessions/{unknown_uuid}",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 404


async def test_get_session_with_invalid_uuid_returns_422(
    seeded_gm, make_db_session_dep
):
    plain, _ = seeded_gm
    app = _make_jdr_app(make_db_session_dep, fakeredis.FakeStrictRedis())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/services/jdr/sessions/not-a-uuid",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 422


# Just to silence "unused import" warnings — datetime is needed for the
# recorded_at fixture values, ImportError would catch missing deps anyway.
_ = datetime
_ = UTC
