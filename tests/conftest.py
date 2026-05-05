"""Shared test fixtures.

Provides the in-memory async SQLite database used by jalon 5+ tests that
need ORM persistence. Older tests (jalons 0-4) ignore these fixtures —
they only kick in when explicitly requested via parameter injection.
"""

from collections.abc import AsyncIterator, Callable

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.db import Base


@pytest_asyncio.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    """In-memory SQLite engine with every ORM table created.

    ``StaticPool`` makes SQLite reuse the same connection across the test,
    otherwise each new connection would see its own empty in-memory DB.
    """
    # Importing the models module registers every table on Base.metadata
    # before we call create_all.
    from app.services.jdr.db import models  # noqa: F401

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A fresh AsyncSession backed by the test engine.

    Use this fixture to seed data or assert on rows directly. For a
    FastAPI dependency override that yields a session per request, use
    :func:`make_db_session_dep`.
    """
    sessionmaker = async_sessionmaker(db_engine, expire_on_commit=False)
    async with sessionmaker() as session:
        yield session


@pytest.fixture
def make_db_session_dep(
    db_engine: AsyncEngine,
) -> Callable[[], AsyncIterator[AsyncSession]]:
    """Return a FastAPI dep override callable for ``get_db_session``.

    Each request gets its own session bound to the in-memory engine, with
    the same commit/rollback lifecycle as production.
    """
    sessionmaker = async_sessionmaker(db_engine, expire_on_commit=False)

    async def _dep() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    return _dep
