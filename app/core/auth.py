"""API key authentication via the Authorization: Bearer header.

ADR 0003: see docs/adr/0003-authentication-strategy.md
"""

import logging
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)
from fastapi import Depends, Request, status

from app.core.config import settings
from app.core.errors import AppError

logger = logging.getLogger(__name__)

_BEARER_PREFIX = "bearer "
_WWW_AUTHENTICATE = 'Bearer realm="ai-kaeyris"'

_hasher = PasswordHasher()


class UnauthorizedError(AppError):
    """Raised when no valid API key is presented."""

    status_code = status.HTTP_401_UNAUTHORIZED
    error_type = "unauthorized"
    title = "Unauthorized"
    default_headers = (("WWW-Authenticate", _WWW_AUTHENTICATE),)


class ForbiddenError(AppError):
    """Raised when an authenticated key is denied access (e.g. revoked)."""

    status_code = status.HTTP_403_FORBIDDEN
    error_type = "forbidden"
    title = "Forbidden"


@dataclass(frozen=True, slots=True)
class APIKeyEntry:
    """A registered API key descriptor: a human-readable name and its Argon2 hash."""

    name: str
    hash: str


@dataclass(frozen=True, slots=True)
class AuthenticatedKey:
    """The result of a successful API key verification."""

    name: str


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


def get_registered_keys() -> list[APIKeyEntry]:
    """Return the list of currently registered API keys.

    Read from settings on each call so tests can monkeypatch / override.
    Production code should never bypass this function — it is the single
    integration point for the key store (env var today, DB at Jalon 5).
    """
    return parse_api_keys(settings.API_KEYS)


def _extract_bearer_token(request: Request) -> str:
    auth_header = request.headers.get("authorization", "")
    if not auth_header or not auth_header.lower().startswith(_BEARER_PREFIX):
        raise UnauthorizedError(detail="Missing or malformed Authorization header.")
    token = auth_header[len(_BEARER_PREFIX) :].strip()
    if not token:
        raise UnauthorizedError(detail="Empty bearer token.")
    return token


def _verify_against_registry(
    token: str, entries: list[APIKeyEntry]
) -> AuthenticatedKey | None:
    """Compare the provided token against each registered Argon2 hash.

    `argon2.PasswordHasher.verify` is itself constant-time per call, so we
    iterate every entry without short-circuiting on the first match's name.
    A skipped malformed hash is logged and ignored (does not bring the API
    down because of one bad config entry).
    """
    matched: AuthenticatedKey | None = None
    for entry in entries:
        try:
            if _hasher.verify(entry.hash, token):
                # Don't break — keep iterating to keep total time uniform across
                # registries of equal size (best-effort timing-attack mitigation).
                if matched is None:
                    matched = AuthenticatedKey(name=entry.name)
        except VerifyMismatchError:
            continue
        except (InvalidHashError, VerificationError) as exc:
            logger.warning(
                "Skipping malformed API key hash for %r: %s", entry.name, exc
            )
            continue
    return matched


def require_api_key(
    request: Request,
    registered_keys: list[APIKeyEntry] = Depends(get_registered_keys),
) -> AuthenticatedKey:
    """FastAPI dependency: enforce a valid Authorization: Bearer header.

    Usage::

        app.include_router(my_service.router, dependencies=[Depends(require_api_key)])

    Raises ``UnauthorizedError`` (401) if no header, malformed header, or
    unknown key. The 401 response carries a ``WWW-Authenticate`` header
    per RFC 6750 §3.
    """
    token = _extract_bearer_token(request)

    if not registered_keys:
        # No keys configured at all → reject every request rather than fail
        # open. Operator must explicitly populate API_KEYS.
        logger.error("API_KEYS is empty: rejecting authenticated request.")
        raise UnauthorizedError(detail="No API keys configured on the server.")

    authenticated = _verify_against_registry(token, registered_keys)
    if authenticated is None:
        raise UnauthorizedError(detail="Invalid API key.")
    return authenticated
