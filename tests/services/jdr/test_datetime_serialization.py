from collections.abc import Callable
from datetime import UTC, datetime
import re
from typing import Any

import fakeredis
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.core.redis_client import get_redis
from app.services.jdr.auth_router import router as auth_router
from app.services.jdr.db.models import ApiKey, ApiKeyStatus, Role
from app.services.jdr.router import router as jdr_router

_TZ_SUFFIX = re.compile(r"(Z|[+-]\d{2}:\d{2})$")


def _make_jdr_app(make_db_session_dep: Callable[..., Any]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(jdr_router)
    app.include_router(auth_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    app.dependency_overrides[get_redis] = lambda: fakeredis.FakeStrictRedis()
    return app


async def _seed_gm(db_session, plain_token: str = "gm-datetime-token") -> ApiKey:
    gm = ApiKey(
        name="gm-datetime",
        hash=PasswordHasher().hash(plain_token),
        role=Role.GM,
        status=ApiKeyStatus.ACTIVE,
    )
    db_session.add(gm)
    await db_session.commit()
    await db_session.refresh(gm)
    return gm


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def assert_explicit_timezone(value: str) -> None:
    assert _TZ_SUFFIX.search(value), f"{value!r} has no explicit timezone suffix"


def assert_same_instant(actual: str, expected: datetime) -> None:
    assert _parse_datetime(actual).astimezone(UTC) == expected.astimezone(UTC)


def assert_datetime_fields_have_explicit_timezone(payload: Any) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.endswith("_at") and value is not None:
                assert isinstance(value, str), f"{key} should be serialized as a string"
                assert_explicit_timezone(value)
            else:
                assert_datetime_fields_have_explicit_timezone(value)
    elif isinstance(payload, list):
        for item in payload:
            assert_datetime_fields_have_explicit_timezone(item)


async def test_session_create_and_detail_emit_explicit_timezone_suffixes(
    db_session,
    make_db_session_dep,
):
    plain = "gm-datetime-session"
    await _seed_gm(db_session, plain)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    submitted = datetime(2026, 5, 31, 18, 0, tzinfo=UTC)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create = await client.post(
            "/services/jdr/sessions",
            json={
                "title": "DIAGNOSTIC-TZ",
                "recorded_at": "2026-05-31T18:00:00.000Z",
                "transcription_mode": "non_diarised",
            },
            headers={"Authorization": f"Bearer {plain}"},
        )
        session_id = create.json()["id"]
        detail = await client.get(
            f"/services/jdr/sessions/{session_id}",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert create.status_code == 201
    assert detail.status_code == 200
    for body in (create.json(), detail.json()):
        for field in ("recorded_at", "created_at", "updated_at"):
            assert_explicit_timezone(body[field])
        assert_same_instant(body["recorded_at"], submitted)


async def test_session_list_emits_explicit_timezone_suffixes(
    db_session,
    make_db_session_dep,
):
    plain = "gm-datetime-list"
    await _seed_gm(db_session, plain)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for title in ("TZ List A", "TZ List B"):
            response = await client.post(
                "/services/jdr/sessions",
                json={"title": title, "recorded_at": "2026-05-31T18:00:00Z"},
                headers={"Authorization": f"Bearer {plain}"},
            )
            assert response.status_code == 201

        listed = await client.get(
            "/services/jdr/sessions",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert listed.status_code == 200
    assert listed.json()["total"] == 2
    assert_datetime_fields_have_explicit_timezone(listed.json())


async def test_pj_create_and_list_emit_explicit_timezone_suffixes(
    db_session,
    make_db_session_dep,
):
    plain = "gm-datetime-pj"
    await _seed_gm(db_session, plain)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/services/jdr/pjs",
            json={"name": "Aragorn"},
            headers={"Authorization": f"Bearer {plain}"},
        )
        listed = await client.get(
            "/services/jdr/pjs",
            headers={"Authorization": f"Bearer {plain}"},
        )

    assert created.status_code == 201
    assert listed.status_code == 200
    assert_datetime_fields_have_explicit_timezone(created.json())
    assert_datetime_fields_have_explicit_timezone(listed.json())


async def test_user_create_and_list_emit_explicit_timezone_suffixes(
    make_db_session_dep,
):
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        setup = await client.post(
            "/services/jdr/auth/setup",
            json={"username": "admin", "password": "admin-password"},
        )
        created = await client.post(
            "/services/jdr/users",
            json={"username": "alice", "profile": "user", "password": "secret"},
        )
        listed = await client.get("/services/jdr/users")

    assert setup.status_code == 201
    assert created.status_code == 201
    assert listed.status_code == 200
    assert_datetime_fields_have_explicit_timezone(setup.json())
    assert_datetime_fields_have_explicit_timezone(created.json())
    assert_datetime_fields_have_explicit_timezone(listed.json())


async def test_session_create_accepts_datetime_input_variants(
    db_session,
    make_db_session_dep,
):
    plain = "gm-datetime-inputs"
    await _seed_gm(db_session, plain)
    app = _make_jdr_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    cases = [
        ("zulu", "2026-05-31T18:00:00Z", datetime(2026, 5, 31, 18, 0, tzinfo=UTC)),
        (
            "offset",
            "2026-05-31T20:00:00+02:00",
            datetime(2026, 5, 31, 18, 0, tzinfo=UTC),
        ),
        ("naive", "2026-05-31T18:00:00", datetime(2026, 5, 31, 18, 0, tzinfo=UTC)),
    ]

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for label, recorded_at, expected in cases:
            response = await client.post(
                "/services/jdr/sessions",
                json={"title": f"TZ input {label}", "recorded_at": recorded_at},
                headers={"Authorization": f"Bearer {plain}"},
            )

            assert response.status_code == 201
            body = response.json()
            assert_explicit_timezone(body["recorded_at"])
            assert_same_instant(body["recorded_at"], expected)
