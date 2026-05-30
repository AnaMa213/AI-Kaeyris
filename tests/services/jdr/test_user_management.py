"""GM user-management endpoint tests."""

from collections.abc import Callable

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.db import get_db_session
from app.core.errors import register_exception_handlers
from app.services.jdr.auth_router import router as auth_router


def _make_app(make_db_session_dep: Callable[..., object]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.dependency_overrides[get_db_session] = make_db_session_dep
    return app


async def _setup_admin(client: AsyncClient) -> None:
    response = await client.post(
        "/services/jdr/auth/setup",
        json={"username": "admin", "password": "admin-password"},
    )
    assert response.status_code == 201


async def test_gm_can_list_users_without_secrets(make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        await client.post(
            "/services/jdr/users",
            json={"username": "alice", "profile": "user", "password": "secret"},
        )
        response = await client.get("/services/jdr/users")

    assert response.status_code == 200
    assert [item["username"] for item in response.json()["items"]] == [
        "admin",
        "alice",
    ]
    assert "password_hash" not in response.text


async def test_gm_can_rotate_password(make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        created = await client.post(
            "/services/jdr/users",
            json={"username": "alice", "profile": "user", "password": "old"},
        )
        user_id = created.json()["id"]
        patched = await client.patch(
            f"/services/jdr/users/{user_id}",
            json={"password": "new"},
        )
        old_login = await client.post(
            "/services/jdr/auth/login",
            json={"username": "alice", "profile": "user", "password": "old"},
        )
        new_login = await client.post(
            "/services/jdr/auth/login",
            json={"username": "alice", "profile": "user", "password": "new"},
        )

    assert patched.status_code == 200
    assert old_login.status_code == 401
    assert new_login.status_code == 200


async def test_delete_is_logical_and_blocks_future_login(make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        created = await client.post(
            "/services/jdr/users",
            json={"username": "alice", "profile": "user", "password": "secret"},
        )
        user_id = created.json()["id"]
        deleted = await client.delete(f"/services/jdr/users/{user_id}")
        login = await client.post(
            "/services/jdr/auth/login",
            json={"username": "alice", "profile": "user", "password": "secret"},
        )
        listed = await client.get("/services/jdr/users")

    assert deleted.status_code == 204
    assert login.status_code == 401
    alice = next(item for item in listed.json()["items"] if item["username"] == "alice")
    assert alice["status"] == "deleted"


async def test_non_gm_user_cannot_manage_users(make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        await client.post(
            "/services/jdr/users",
            json={"username": "alice", "profile": "user", "password": "secret"},
        )
        await client.post(
            "/services/jdr/auth/login",
            json={"username": "alice", "profile": "user", "password": "secret"},
        )
        response = await client.get("/services/jdr/users")

    assert response.status_code == 403


async def test_cannot_delete_last_active_gm(make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        listed = await client.get("/services/jdr/users")
        admin_id = listed.json()["items"][0]["id"]
        response = await client.delete(f"/services/jdr/users/{admin_id}")

    assert response.status_code == 409
