"""Business logic for the JDR service.

ADR 0006 §5. Pure orchestrators that compose repositories and adapters;
no FastAPI imports here so the same functions are reusable from jobs
(``app/jobs/jdr.py``) without going through HTTP.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.jdr.db.models import Session
from app.services.jdr.db.repositories import SessionRepository


async def create_session(
    db: AsyncSession,
    *,
    title: str,
    recorded_at: datetime,
    gm_key_id: UUID,
) -> Session:
    """Persist a new session owned by the given GM key.

    Initial ``state`` is ``created`` (default in the ORM model); ``mode``
    is ``batch`` until the live mode is implemented (FR-015/016 are stubs
    at jalon 5 — see ADR 0006 §4).
    """
    return await SessionRepository(db).create(
        title=title,
        recorded_at=recorded_at,
        gm_key_id=gm_key_id,
    )


async def list_sessions(
    db: AsyncSession, *, gm_key_id: UUID
) -> list[Session]:
    """Return every session owned by the given GM, oldest first."""
    return await SessionRepository(db).list_for_gm(gm_key_id)


async def get_session(
    db: AsyncSession, *, session_id: UUID, gm_key_id: UUID
) -> Session | None:
    """Return a session if and only if it belongs to the GM (FR-014 isolation)."""
    return await SessionRepository(db).get_for_gm(session_id, gm_key_id)
