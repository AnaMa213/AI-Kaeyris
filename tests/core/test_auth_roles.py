"""Auth roles (gm/player) and env-var bootstrap (jalon 5 — ADR 0006 §3)."""

import uuid
from collections.abc import Callable
from typing import Annotated

import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    AuthenticatedKey,
    bootstrap_api_keys_from_env,
    require_api_key,
    require_gm,
    require_player,
)
from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.services.jdr.db.models import ApiKey, ApiKeyStatus, Pj, Role


# ---------------------------------------------------------------------------
# Bootstrap: import API_KEYS env var into DB on first startup
# ---------------------------------------------------------------------------


async def test_bootstrap_imports_env_var_when_table_is_empty(
    db_session: AsyncSession, monkeypatch
):
    plain_one = "bootstrap-key-one"
    plain_two = "bootstrap-key-two"
    hasher = PasswordHasher()
    env_value = (
        f"laptop:{hasher.hash(plain_one)};pi-monitor:{hasher.hash(plain_two)}"
    )
    monkeypatch.setattr("app.core.auth.settings.API_KEYS", env_value)

    inserted = await bootstrap_api_keys_from_env(db_session)

    assert inserted == 2
    rows = (await db_session.scalars(select(ApiKey).order_by(ApiKey.name))).all()
    assert [r.name for r in rows] == ["laptop", "pi-monitor"]
    assert all(r.role == Role.GM for r in rows)
    assert all(r.status == ApiKeyStatus.ACTIVE for r in rows)
    assert all(r.pj_id is None for r in rows)


async def test_bootstrap_is_noop_when_env_var_is_empty(
    db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr("app.core.auth.settings.API_KEYS", "")
    inserted = await bootstrap_api_keys_from_env(db_session)
    assert inserted == 0
    assert (await db_session.scalar(select(ApiKey.id))) is None


async def test_bootstrap_is_noop_when_table_already_has_keys(
    db_session: AsyncSession, monkeypatch
):
    # Pre-seed one key — bootstrap must not duplicate or replace it.
    db_session.add(
        ApiKey(
            name="pre-existing",
            hash=PasswordHasher().hash("whatever"),
            role=Role.GM,
            status=ApiKeyStatus.ACTIVE,
        )
    )
    await db_session.commit()

    monkeypatch.setattr(
        "app.core.auth.settings.API_KEYS",
        f"new-key:{PasswordHasher().hash('xxx')}",
    )
    inserted = await bootstrap_api_keys_from_env(db_session)

    assert inserted == 0
    rows = (await db_session.scalars(select(ApiKey))).all()
    assert [r.name for r in rows] == ["pre-existing"]


# ---------------------------------------------------------------------------
# Player keys: pj_id is mandatory; misconfigured player keys are skipped
# ---------------------------------------------------------------------------


def _make_app(make_db_session_dep: Callable[..., object]) -> FastAPI:
    """A protected route that just echoes the authenticated identity."""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/protected")
    async def _protected(
        auth: Annotated[AuthenticatedKey, Depends(require_api_key)],
    ) -> dict[str, str | None]:
        return {
            "name": auth.name,
            "role": auth.role.value,
            "pj_id": str(auth.pj_id) if auth.pj_id else None,
        }

    app.dependency_overrides[get_db_session] = make_db_session_dep
    return app


@pytest_asyncio.fixture
async def seeded_pj(db_session: AsyncSession) -> Pj:
    """Create a GM key + a PJ owned by it. Returns the PJ row."""
    gm = ApiKey(
        name="gm-owner",
        hash=PasswordHasher().hash("gm-token"),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
    )
    db_session.add(gm)
    await db_session.flush()
    pj = Pj(id=uuid.uuid4(), name="Aragorn", owner_gm_key_id=gm.id)
    db_session.add(pj)
    await db_session.commit()
    await db_session.refresh(pj)
    return pj


async def test_valid_player_key_is_accepted(
    db_session, make_db_session_dep, seeded_pj
):
    """A player key with a valid pj_id authenticates and exposes its PJ."""
    plain = "player-token-aragorn"
    db_session.add(
        ApiKey(
            name="player-aragorn",
            hash=PasswordHasher().hash(plain),
            role=Role.PLAYER,
            status=ApiKeyStatus.ACTIVE,
            pj_id=seeded_pj.id,
        )
    )
    await db_session.commit()

    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/protected", headers={"Authorization": f"Bearer {plain}"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "player"
    assert body["pj_id"] == str(seeded_pj.id)


async def test_player_key_without_pj_id_is_skipped(
    db_session, make_db_session_dep
):
    """A misconfigured player row (no pj_id) is ignored — no auth granted."""
    plain = "broken-player-token"
    db_session.add(
        ApiKey(
            name="broken-player",
            hash=PasswordHasher().hash(plain),
            role=Role.PLAYER,
            status=ApiKeyStatus.ACTIVE,
            pj_id=None,  # invalid per FR-014a — must be skipped
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


# ---------------------------------------------------------------------------
# require_role: gm-only and player-only routes
# ---------------------------------------------------------------------------


def _make_app_with_role_routes(
    make_db_session_dep: Callable[..., object],
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/gm-only", dependencies=[Depends(require_gm)])
    async def _gm_only() -> dict[str, str]:
        return {"area": "gm"}

    @app.get("/player-only", dependencies=[Depends(require_player)])
    async def _player_only() -> dict[str, str]:
        return {"area": "player"}

    app.dependency_overrides[get_db_session] = make_db_session_dep
    return app


@pytest_asyncio.fixture
async def gm_token_and_player_token(
    db_session: AsyncSession, seeded_pj: Pj
) -> tuple[str, str]:
    """Insert a GM key AND a player key bound to seeded_pj. Return both plaintext tokens."""
    gm_plain = "gm-only-secret-token"
    db_session.add(
        ApiKey(
            name="another-gm",
            hash=PasswordHasher().hash(gm_plain),
            role=Role.GM,
            status=ApiKeyStatus.ACTIVE,
        )
    )
    player_plain = "player-only-secret-token"
    db_session.add(
        ApiKey(
            name="another-player",
            hash=PasswordHasher().hash(player_plain),
            role=Role.PLAYER,
            status=ApiKeyStatus.ACTIVE,
            pj_id=seeded_pj.id,
        )
    )
    await db_session.commit()
    return gm_plain, player_plain


async def test_gm_only_route_rejects_player_with_403(
    gm_token_and_player_token, make_db_session_dep
):
    _gm_token, player_token = gm_token_and_player_token
    app = _make_app_with_role_routes(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/gm-only", headers={"Authorization": f"Bearer {player_token}"}
        )

    assert response.status_code == 403
    body = response.json()
    assert body["type"] == "https://kaeyris.local/errors/forbidden"
    assert "gm" in body["detail"].lower()


async def test_player_only_route_rejects_gm_with_403(
    gm_token_and_player_token, make_db_session_dep
):
    gm_token, _player_token = gm_token_and_player_token
    app = _make_app_with_role_routes(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/player-only", headers={"Authorization": f"Bearer {gm_token}"}
        )

    assert response.status_code == 403


async def test_gm_only_route_allows_gm(
    gm_token_and_player_token, make_db_session_dep
):
    gm_token, _ = gm_token_and_player_token
    app = _make_app_with_role_routes(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/gm-only", headers={"Authorization": f"Bearer {gm_token}"}
        )

    assert response.status_code == 200
    assert response.json() == {"area": "gm"}


async def test_player_only_route_allows_player(
    gm_token_and_player_token, make_db_session_dep
):
    _, player_token = gm_token_and_player_token
    app = _make_app_with_role_routes(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/player-only", headers={"Authorization": f"Bearer {player_token}"}
        )

    assert response.status_code == 200
    assert response.json() == {"area": "player"}
