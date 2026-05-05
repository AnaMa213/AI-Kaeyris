"""Tests for the DB-backed API key authentication.

ADR 0003 (jalon 2 — initial design) and ADR 0006 §3 (jalon 5 — DB-backed
registry, gm/player roles, env-var bootstrap).
"""

from collections.abc import Callable
from typing import Annotated

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    APIKeyEntry,
    AuthenticatedKey,
    parse_api_keys,
    require_api_key,
)
from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.services.jdr.db.models import ApiKey, ApiKeyStatus, Role


# ---------------------------------------------------------------------------
# parse_api_keys (unchanged from jalon 2 — used only for the bootstrap path)
# ---------------------------------------------------------------------------


def test_parse_empty_returns_no_entries():
    assert parse_api_keys("") == []
    assert parse_api_keys(None) == []
    assert parse_api_keys("   ") == []


def test_parse_single_entry():
    entries = parse_api_keys("laptop:$argon2id$v=19$m=64,t=3,p=1$abc$def")
    assert entries == [
        APIKeyEntry(name="laptop", hash="$argon2id$v=19$m=64,t=3,p=1$abc$def")
    ]


def test_parse_multiple_entries_split_on_semicolon():
    entries = parse_api_keys(
        "laptop:$argon2id$v=19$m=64,t=3,p=1$a$b;pi:$argon2id$v=19$m=64,t=3,p=1$c$d"
    )
    assert [e.name for e in entries] == ["laptop", "pi"]
    assert all("$argon2id" in e.hash for e in entries)


def test_parse_rejects_malformed_entries():
    with pytest.raises(ValueError, match="Invalid API_KEYS"):
        parse_api_keys("noseparator")
    with pytest.raises(ValueError, match="Invalid API_KEYS"):
        parse_api_keys("nameonly:")
    with pytest.raises(ValueError, match="Invalid API_KEYS"):
        parse_api_keys(":hashonly")


# ---------------------------------------------------------------------------
# require_api_key — DB-backed lookup
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seeded_gm_key(db_session: AsyncSession) -> tuple[str, ApiKey]:
    """Insert one active GM key in the in-memory DB. Returns (plaintext, row)."""
    plain = "test-secret-key-do-not-use-in-prod"
    hashed = PasswordHasher().hash(plain)
    api_key = ApiKey(
        name="test-gm",
        hash=hashed,
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
        pj_id=None,
    )
    db_session.add(api_key)
    await db_session.commit()
    await db_session.refresh(api_key)
    return plain, api_key


def _make_app(
    make_db_session_dep: Callable[..., object],
) -> FastAPI:
    """Mini FastAPI app with a single protected route."""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/protected")
    async def _protected(
        auth: Annotated[AuthenticatedKey, Depends(require_api_key)],
    ) -> dict[str, str]:
        return {"hello": auth.name, "role": auth.role.value}

    app.dependency_overrides[get_db_session] = make_db_session_dep
    return app


async def test_missing_authorization_header_returns_401(
    seeded_gm_key, make_db_session_dep
):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/protected")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer realm="ai-kaeyris"'
    body = response.json()
    assert body["type"] == "https://kaeyris.local/errors/unauthorized"
    assert body["status"] == 401
    assert body["instance"] == "/protected"


async def test_malformed_authorization_header_returns_401(
    seeded_gm_key, make_db_session_dep
):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/protected", headers={"Authorization": "Basic something"}
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer realm="ai-kaeyris"'


async def test_unknown_key_returns_401(seeded_gm_key, make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/protected", headers={"Authorization": "Bearer wrong-key"}
        )

    assert response.status_code == 401


async def test_valid_key_returns_200_with_role(seeded_gm_key, make_db_session_dep):
    plain, _row = seeded_gm_key
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/protected", headers={"Authorization": f"Bearer {plain}"}
        )

    assert response.status_code == 200
    assert response.json() == {"hello": "test-gm", "role": "gm"}


async def test_empty_registry_rejects_even_with_a_token(make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/protected", headers={"Authorization": "Bearer anything"}
        )

    assert response.status_code == 401
    assert response.json()["status"] == 401


async def test_revoked_key_is_rejected(db_session, make_db_session_dep):
    """A revoked key is not in the active set; same outcome as unknown key."""
    plain = "revoked-key-do-not-use"
    hashed = PasswordHasher().hash(plain)
    db_session.add(
        ApiKey(
            name="revoked-gm",
            hash=hashed,
            role=Role.GM,
            status=ApiKeyStatus.REVOKED,
            pj_id=None,
        )
    )
    await db_session.commit()

    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/protected", headers={"Authorization": f"Bearer {plain}"}
        )

    assert response.status_code == 401
