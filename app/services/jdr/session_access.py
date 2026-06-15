"""Session-level authorization helpers for the JDR service."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedKey
from app.services.jdr.db.models import CampaignRole, Session
from app.services.jdr.db.repositories import CampaignRepository, SessionRepository


async def resolve_session_for_gm(
    db: AsyncSession,
    *,
    session_id: UUID,
    auth: AuthenticatedKey,
    require_gm_role: bool = True,
) -> Session | None:
    """Return a session visible to the current GM-like caller.

    Object-level checks must use the requested session's campaign, not the
    caller's current/default campaign. The legacy ``gm_key_id`` boundary stays
    in place because several JDR tables still use it as their owner key.
    """
    session = await SessionRepository(db).get_for_gm(
        session_id,
        auth.id,
        campaign_id=None,
    )
    if session is None:
        return None

    if auth.source != "web_session" or auth.user_id is None:
        return session

    if session.campaign_id is None:
        return session

    membership = await CampaignRepository(db).get_membership(
        user_id=auth.user_id,
        campaign_id=session.campaign_id,
    )
    if membership is None:
        return None
    if require_gm_role and membership.role is not CampaignRole.GM:
        return None
    return session
