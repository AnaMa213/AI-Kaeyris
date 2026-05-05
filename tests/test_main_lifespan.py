"""Wiring test: the FastAPI lifespan triggers the env-var → DB bootstrap.

The bootstrap function itself is exhaustively covered by
``tests/core/test_auth_roles.py``. This file only verifies that the
plumbing in ``app/main.py`` actually calls it on startup.
"""

import pytest_asyncio
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.jdr.db.models import ApiKey


@pytest_asyncio.fixture
async def patched_main_app(db_engine, monkeypatch):
    """Re-use the production ``app`` but with the test engine wired in.

    Patches ``app.main.get_sessionmaker`` so the lifespan's bootstrap
    talks to the in-memory DB rather than the real one. We import the
    app lazily so the monkeypatch lands before the lifespan reads it.
    """
    sessionmaker = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.main.get_sessionmaker", lambda: sessionmaker)
    monkeypatch.setattr(
        "app.core.auth.settings.API_KEYS",
        f"lifespan-test:{PasswordHasher().hash('whatever')}",
    )
    from app.main import app

    return app, sessionmaker


def test_lifespan_imports_env_var_keys_into_empty_db(patched_main_app):
    app, sessionmaker = patched_main_app

    # Entering the TestClient context manager runs the lifespan startup;
    # exiting it runs shutdown.
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200

    # After lifespan, the bootstrap should have inserted the env-var key.
    import asyncio

    async def _check() -> list[str]:
        async with sessionmaker() as session:
            rows = (await session.scalars(select(ApiKey))).all()
            return [r.name for r in rows]

    names = asyncio.run(_check())
    assert names == ["lifespan-test"]
