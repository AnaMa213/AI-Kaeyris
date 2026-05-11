"""API key authentication via the Authorization: Bearer header.

ADR 0003 (jalon 2 — initial Bearer + Argon2 design)
ADR 0006 §3 (jalon 5 — DB-backed registry, gm/player roles, env-var bootstrap)

The runtime registry now lives in the ``jdr_api_keys`` table. The legacy
``API_KEYS`` env var (jalon 2) becomes a one-shot bootstrap source: at
startup, if the table is empty, every entry is imported with role ``gm``
so an existing deployment keeps working without manual migration.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)
from fastapi import Depends, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db_session
from app.core.errors import AppError
from app.services.jdr.db.models import ApiKey, ApiKeyStatus, Role

logger = logging.getLogger(__name__)

_BEARER_PREFIX = "bearer "
_WWW_AUTHENTICATE = 'Bearer realm="ai-kaeyris"'

_hasher = PasswordHasher()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class UnauthorizedError(AppError):
    """Raised when no valid API key is presented."""

    status_code = status.HTTP_401_UNAUTHORIZED
    error_type = "unauthorized"
    title = "Unauthorized"
    default_headers = (("WWW-Authenticate", _WWW_AUTHENTICATE),)


class ForbiddenError(AppError):
    """Raised when an authenticated key is denied access (revoked, wrong role)."""

    status_code = status.HTTP_403_FORBIDDEN
    error_type = "forbidden"
    title = "Forbidden"


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class APIKeyEntry:
    """Bootstrap descriptor used only for the env-var → DB import.

    Production lookup goes through the DB row (``ApiKey``), not this type.
    """

    name: str
    hash: str


@dataclass(frozen=True, slots=True)
class AuthenticatedKey:
    """The result of a successful API key verification.

    Carries everything authorisation logic needs: the row id (used as
    ownership key by business tables — sessions, pjs, …), the human name
    (logging and audit), the role (``gm`` or ``player``), and ``pj_id``
    when the role is ``player`` (the PJ the player is bound to).
    """

    id: UUID
    name: str
    role: Role
    pj_id: UUID | None


# ---------------------------------------------------------------------------
# Env-var parsing (kept for the bootstrap path only)
# ---------------------------------------------------------------------------


def parse_api_keys(raw: str | None) -> list[APIKeyEntry]:
    """Parse the API_KEYS env var into structured entries.

    Format: ``name1:hash1;name2:hash2`` (semicolon separator, since Argon2
    hashes contain commas in their parameter section).
    """
    if not raw:
        return []
    entries: list[APIKeyEntry] = []
    for raw_part in raw.split(";"):
        part = raw_part.strip()
        if not part:
            continue
        name, sep, hash_value = part.partition(":")
        if not sep or not name or not hash_value:
            raise ValueError(f"Invalid API_KEYS entry (expected 'name:hash'): {part!r}")
        entries.append(APIKeyEntry(name=name.strip(), hash=hash_value.strip()))
    return entries


# ---------------------------------------------------------------------------
# Bootstrap (env var → DB)
# ---------------------------------------------------------------------------


async def bootstrap_api_keys_from_env(session: AsyncSession) -> int:
    """Import API_KEYS env var entries into the DB if the table is empty.

    Returns the number of rows inserted. No-op if the table already has
    at least one row (production restarts must not duplicate keys).
    Logs a warning when API_KEYS is set but the table is non-empty.
    """
    existing = await session.scalar(select(ApiKey.id).limit(1))
    if existing is not None:
        if settings.API_KEYS:
            logger.info(
                "Bootstrap skipped: jdr_api_keys already populated; "
                "API_KEYS env var ignored."
            )
        return 0

    entries = parse_api_keys(settings.API_KEYS)
    if not entries:
        return 0

    for entry in entries:
        session.add(
            ApiKey(
                name=entry.name,
                hash=entry.hash,
                role=Role.GM,
                status=ApiKeyStatus.ACTIVE,
                pj_id=None,
            )
        )
    await session.commit()
    logger.info("Bootstrap imported %d API key(s) from env var.", len(entries))
    return len(entries)


# ---------------------------------------------------------------------------
# Bearer extraction
# ---------------------------------------------------------------------------


def _extract_bearer_token(request: Request) -> str:
    auth_header = request.headers.get("authorization", "")
    if not auth_header or not auth_header.lower().startswith(_BEARER_PREFIX):
        raise UnauthorizedError(detail="Missing or malformed Authorization header.")
    token = auth_header[len(_BEARER_PREFIX) :].strip()
    if not token:
        raise UnauthorizedError(detail="Empty bearer token.")
    return token


# ---------------------------------------------------------------------------
# DB-backed verification
# ---------------------------------------------------------------------------


async def _list_active_keys(session: AsyncSession) -> Sequence[ApiKey]:
    """Fetch every active key. Order matters for nothing — see the
    constant-time comment in `_verify_against_registry`."""
    stmt = select(ApiKey).where(ApiKey.status == ApiKeyStatus.ACTIVE)
    result = await session.scalars(stmt)
    return result.all()


def _verify_against_registry(
    token: str, entries: Sequence[ApiKey]
) -> AuthenticatedKey | None:
    """Compare the provided token against each registered Argon2 hash.

    ``argon2.PasswordHasher.verify`` is constant-time per call; we iterate
    every entry without short-circuiting on the first match's name to keep
    the total time uniform across registries of equal size (best-effort
    timing-attack mitigation).

    A row whose ``role='player'`` lacks a ``pj_id`` is skipped: it can't
    be used (FR-014a) and we never want to grant access to a misconfigured
    key. The same goes for malformed Argon2 hashes.
    """
    matched: AuthenticatedKey | None = None
    for entry in entries:
        if entry.role == Role.PLAYER and entry.pj_id is None:
            logger.warning(
                "Skipping player key %r without pj_id — invalid registry row.",
                entry.name,
            )
            continue
        try:
            if _hasher.verify(entry.hash, token):
                if matched is None:
                    matched = AuthenticatedKey(
                        id=entry.id,
                        name=entry.name,
                        role=entry.role,
                        pj_id=entry.pj_id,
                    )
        except VerifyMismatchError:
            continue
        except (InvalidHashError, VerificationError) as exc:
            logger.warning(
                "Skipping malformed API key hash for %r: %s", entry.name, exc
            )
            continue
    return matched


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


async def require_api_key(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthenticatedKey:
    """FastAPI dependency: enforce a valid Authorization: Bearer header.

    Lookup is DB-backed (``jdr_api_keys`` table). The 401 response carries
    a ``WWW-Authenticate`` header per RFC 6750 §3.
    """
    token = _extract_bearer_token(request)

    entries = await _list_active_keys(session)
    if not entries:
        # Fail closed: an empty registry rejects every request.
        logger.error("jdr_api_keys is empty: rejecting authenticated request.")
        raise UnauthorizedError(detail="No API keys configured on the server.")

    authenticated = _verify_against_registry(token, entries)
    if authenticated is None:
        raise UnauthorizedError(detail="Invalid API key.")
    return authenticated


def require_role(
    role: Role,
) -> "Callable[[AuthenticatedKey], AuthenticatedKey]":  # type: ignore[name-defined]
    """Build a dependency that requires a specific role.

    Usage::

        from app.core.auth import require_role
        from app.services.jdr.db.models import Role

        @router.get("/admin", dependencies=[Depends(require_role(Role.GM))])
        ...
    """

    def _dep(
        auth: Annotated[AuthenticatedKey, Depends(require_api_key)],
    ) -> AuthenticatedKey:
        if auth.role != role:
            raise ForbiddenError(
                detail=f"This endpoint requires role {role.value!r}.",
            )
        return auth

    return _dep


# Convenience aliases — used by routers as `dependencies=[Depends(require_gm)]`.
require_gm = require_role(Role.GM)
require_player = require_role(Role.PLAYER)


# ---------------------------------------------------------------------------
# Type hint backfill (kept at the bottom to avoid forward reference noise)
# ---------------------------------------------------------------------------

from collections.abc import Callable  # noqa: E402

require_role.__annotations__["return"] = Callable[[AuthenticatedKey], AuthenticatedKey]
