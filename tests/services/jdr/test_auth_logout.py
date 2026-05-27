"""Logout endpoint tests."""

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


async def test_logout_revokes_current_cookie(make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/services/jdr/auth/setup",
            json={"username": "admin", "password": "admin-password"},
        )
        logout = await client.post("/services/jdr/auth/logout")
        users_after_logout = await client.get("/services/jdr/users")

    assert logout.status_code == 204
    assert "session=" in logout.headers["set-cookie"]
    assert "max-age=0" in logout.headers["set-cookie"].lower()
    assert users_after_logout.status_code == 401
