"""US5 — live mode is a documented stub at Jalon 5 (FR-015 / FR-016).

POST /services/jdr/live/sessions must return 501 with a RFC 9457
Problem Details whose ``type`` URI ends with ``errors/live-not-implemented``.
The route must also be listed in the OpenAPI schema so the contract is
publicly visible before any implementation lands (Jalon 6+).
"""

from collections.abc import Callable
from typing import Any
from uuid import uuid4

import fakeredis
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.redis_client import get_redis
from app.services.jdr.db.models import ApiKey, ApiKeyStatus, Role
from app.services.jdr.router import router as jdr_router


def _make_jdr_app(make_db_session_dep: Callable[..., Any]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(jdr_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = lambda: fakeredis.FakeStrictRedis()
    return app


async def _seed_gm(db_session, plain_token: str) -> ApiKey:
    gm = ApiKey(
        name=f"gm-{uuid4().hex[:8]}",
        hash=PasswordHasher().hash(plain_token),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
    )
    db_session.add(gm)
    await db_session.commit()
    await db_session.refresh(gm)
    return gm


async def test_post_live_sessions_returns_501_problem_details(
    db_session, make_db_session_dep
):
    plain = "gm-live"
    await _seed_gm(db_session, plain)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/services/jdr/live/sessions",
            json={"title": "live session test"},
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert response.status_code == 501
    body = response.json()
    # RFC 9457 Problem Details shape
    assert body["type"].endswith("/live-not-implemented")
    assert body["status"] == 501
    assert "title" in body
    assert "detail" in body


async def test_live_endpoint_listed_in_openapi(
    db_session, make_db_session_dep
):
    """The OpenAPI schema must surface ``/live/sessions`` even though it
    only ever returns 501 — the contract is published before the impl."""
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        spec = (await client.get("/openapi.json")).json()
    paths = spec.get("paths", {})
    assert "/services/jdr/live/sessions" in paths, (
        "Live stub route must be visible in OpenAPI for contract discoverability."
    )
