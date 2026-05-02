import pytest
from argon2 import PasswordHasher
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.auth import (
    APIKeyEntry,
    AuthenticatedKey,
    get_registered_keys,
    parse_api_keys,
    require_api_key,
)
from app.core.errors import register_exception_handlers


# ---- parse_api_keys ---------------------------------------------------------


def test_parse_empty_returns_no_entries():
    assert parse_api_keys("") == []
    assert parse_api_keys(None) == []
    assert parse_api_keys("   ") == []


def test_parse_single_entry():
    entries = parse_api_keys("laptop:$argon2id$v=19$m=64,t=3,p=1$abc$def")
    assert entries == [
        APIKeyEntry(name="laptop", hash="$argon2id$v=19$m=64,t=3,p=1$abc$def")
    ]


def test_parse_multiple_entries_split_on_semicolon():
    entries = parse_api_keys(
        "laptop:$argon2id$v=19$m=64,t=3,p=1$a$b;pi:$argon2id$v=19$m=64,t=3,p=1$c$d"
    )
    assert [e.name for e in entries] == ["laptop", "pi"]
    assert all("$argon2id" in e.hash for e in entries)


def test_parse_rejects_malformed_entries():
    with pytest.raises(ValueError, match="Invalid API_KEYS"):
        parse_api_keys("noseparator")
    with pytest.raises(ValueError, match="Invalid API_KEYS"):
        parse_api_keys("nameonly:")
    with pytest.raises(ValueError, match="Invalid API_KEYS"):
        parse_api_keys(":hashonly")


# ---- require_api_key (FastAPI dependency) ----------------------------------


@pytest.fixture
def known_key() -> tuple[str, list[APIKeyEntry]]:
    """Return a (plaintext_key, registry) pair for tests."""
    key = "test-secret-key-do-not-use-in-prod"
    hashed = PasswordHasher().hash(key)
    return key, [APIKeyEntry(name="test", hash=hashed)]


def _make_app(registry: list[APIKeyEntry]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/protected")
    def _protected(auth: AuthenticatedKey = Depends(require_api_key)) -> dict[str, str]:
        return {"hello": auth.name}

    app.dependency_overrides[get_registered_keys] = lambda: registry
    return app


async def test_missing_authorization_header_returns_401(known_key):
    _, registry = known_key
    app = _make_app(registry)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/protected")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer realm="ai-kaeyris"'
    body = response.json()
    assert body["type"] == "https://kaeyris.local/errors/unauthorized"
    assert body["status"] == 401
    assert body["instance"] == "/protected"


async def test_malformed_authorization_header_returns_401(known_key):
    _, registry = known_key
    app = _make_app(registry)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/protected", headers={"Authorization": "Basic something"}
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer realm="ai-kaeyris"'


async def test_unknown_key_returns_401(known_key):
    _, registry = known_key
    app = _make_app(registry)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/protected", headers={"Authorization": "Bearer wrong-key"}
        )

    assert response.status_code == 401


async def test_valid_key_returns_200_and_authenticated_name(known_key):
    plain, registry = known_key
    app = _make_app(registry)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/protected", headers={"Authorization": f"Bearer {plain}"}
        )

    assert response.status_code == 200
    assert response.json() == {"hello": "test"}


async def test_empty_registry_rejects_even_with_a_token():
    app = _make_app([])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/protected", headers={"Authorization": "Bearer anything"}
        )

    assert response.status_code == 401
    assert response.json()["status"] == 401
