"""US1 / feature 002 — Création de session avec `transcription_mode`.

Tests TDD AVANT impl : payload `POST /sessions` accepte le mode, défaut
`diarised` si absent (non-régression Jalon 5), valeurs invalides → 422,
immuabilité via PATCH → 422.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import fakeredis
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.models import Profile, User, UserStatus
from app.core.redis_client import get_redis
from app.core.users import hash_password
from app.services.jdr.db.models import ApiKey, ApiKeyStatus, Campaign, Role
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
    await db_session.flush()
    user = User(
        username=f"gm-{uuid4().hex[:8]}",
        profile=Profile.GM,
        password_hash=hash_password("password"),
        status=UserStatus.ACTIVE,
        api_key_id=gm.id,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(user)
    await db_session.flush()
    campaign = Campaign(name=f"Campaign {uuid4().hex[:8]}", owner_user_id=user.id)
    db_session.add(campaign)
    await db_session.commit()
    await db_session.refresh(gm)
    gm.test_campaign_id = campaign.id
    return gm


def _new_session_payload(**overrides):
    base = {
        "title": "Session mode test",
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# POST /sessions — transcription_mode
# ---------------------------------------------------------------------------


async def test_post_session_default_mode_is_diarised(
    db_session, make_db_session_dep
):
    plain = "gm-default-mode"
    gm = await _seed_gm(db_session, plain)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/services/jdr/sessions",
            json=_new_session_payload(campaign_id=str(gm.test_campaign_id)),
            headers={"Authorization": f"Bearer {plain}"},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["transcription_mode"] == "diarised"


async def test_post_session_explicit_non_diarised(
    db_session, make_db_session_dep
):
    plain = "gm-non-diarised"
    gm = await _seed_gm(db_session, plain)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/services/jdr/sessions",
            json=_new_session_payload(
                campaign_id=str(gm.test_campaign_id),
                transcription_mode="non_diarised",
            ),
            headers={"Authorization": f"Bearer {plain}"},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["transcription_mode"] == "non_diarised"


async def test_post_session_invalid_mode_returns_422(
    db_session, make_db_session_dep
):
    plain = "gm-invalid-mode"
    gm = await _seed_gm(db_session, plain)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/services/jdr/sessions",
            json=_new_session_payload(
                campaign_id=str(gm.test_campaign_id),
                transcription_mode="garbage",
            ),
            headers={"Authorization": f"Bearer {plain}"},
        )
    assert response.status_code == 422


async def test_get_session_exposes_transcription_mode(
    db_session, make_db_session_dep
):
    plain = "gm-get-mode"
    gm = await _seed_gm(db_session, plain)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/services/jdr/sessions",
            json=_new_session_payload(
                campaign_id=str(gm.test_campaign_id),
                transcription_mode="non_diarised",
            ),
            headers={"Authorization": f"Bearer {plain}"},
        )
        sid = created.json()["id"]
        got = await client.get(
            f"/services/jdr/sessions/{sid}",
            headers={"Authorization": f"Bearer {plain}"},
        )
    assert got.status_code == 200
    assert got.json()["transcription_mode"] == "non_diarised"


# ---------------------------------------------------------------------------
# PATCH /sessions — immutability of transcription_mode
# ---------------------------------------------------------------------------


async def test_patch_session_rejects_transcription_mode_with_422(
    db_session, make_db_session_dep
):
    """FR-002 — `transcription_mode` est immuable après création."""
    plain = "gm-patch-mode"
    gm = await _seed_gm(db_session, plain)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/services/jdr/sessions",
            json=_new_session_payload(
                campaign_id=str(gm.test_campaign_id),
                transcription_mode="non_diarised",
            ),
            headers={"Authorization": f"Bearer {plain}"},
        )
        sid = created.json()["id"]
        # Tentative de modifier le mode (même avec la même valeur courante)
        patched = await client.patch(
            f"/services/jdr/sessions/{sid}",
            json={"transcription_mode": "diarised"},
            headers={"Authorization": f"Bearer {plain}"},
        )
    assert patched.status_code == 422
    body = patched.json()
    assert body["type"].endswith("/immutable-field")


async def test_patch_session_title_still_works(
    db_session, make_db_session_dep
):
    """Regression — les champs Jalon 5 (title, campaign_context) restent modifiables."""
    plain = "gm-patch-title"
    gm = await _seed_gm(db_session, plain)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/services/jdr/sessions",
            json=_new_session_payload(campaign_id=str(gm.test_campaign_id)),
            headers={"Authorization": f"Bearer {plain}"},
        )
        sid = created.json()["id"]
        patched = await client.patch(
            f"/services/jdr/sessions/{sid}",
            json={"title": "Renommée"},
            headers={"Authorization": f"Bearer {plain}"},
        )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Renommée"
