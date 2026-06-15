"""AI model settings endpoint tests (BD-18 model config)."""

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


async def test_admin_gets_default_model_settings(make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        response = await client.get("/services/jdr/settings/models")

    assert response.status_code == 200
    assert response.json() == {
        "transcription_provider": "cloud",
        "summary_provider": "cloud",
        "transcription_local_path": None,
        "summary_local_path": None,
        "transcription_cloud_model": None,
        "summary_cloud_model": None,
        "deepinfra_api_key_set": False,
    }


async def test_admin_can_persist_model_settings(make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        patched = await client.patch(
            "/services/jdr/settings/models",
            json={
                "transcription_provider": "local",
                "summary_provider": "ollama",
            },
        )
        fetched = await client.get("/services/jdr/settings/models")

    assert patched.status_code == 200
    assert patched.json() == {
        "transcription_provider": "local",
        "summary_provider": "ollama",
        "transcription_local_path": None,
        "summary_local_path": None,
        "transcription_cloud_model": None,
        "summary_cloud_model": None,
        "deepinfra_api_key_set": False,
    }
    assert fetched.status_code == 200
    assert fetched.json() == patched.json()


async def test_admin_can_persist_local_model_paths(make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        patched = await client.patch(
            "/services/jdr/settings/models",
            json={
                "transcription_provider": "local",
                "transcription_local_path": "/models/whisper-large-v3",
            },
        )
        fetched = await client.get("/services/jdr/settings/models")

    assert patched.status_code == 200
    assert patched.json() == {
        "transcription_provider": "local",
        "summary_provider": "cloud",
        "transcription_local_path": "/models/whisper-large-v3",
        "summary_local_path": None,
        "transcription_cloud_model": None,
        "summary_cloud_model": None,
        "deepinfra_api_key_set": False,
    }
    assert fetched.status_code == 200
    assert fetched.json() == patched.json()


async def test_patch_model_settings_is_partial(make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        first = await client.patch(
            "/services/jdr/settings/models",
            json={
                "transcription_provider": "local",
                "summary_provider": "ollama",
                "transcription_local_path": "/models/whisper-large-v3",
            },
        )
        second = await client.patch(
            "/services/jdr/settings/models",
            json={"summary_provider": "cloud"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == {
        "transcription_provider": "local",
        "summary_provider": "cloud",
        "transcription_local_path": "/models/whisper-large-v3",
        "summary_local_path": None,
        "transcription_cloud_model": None,
        "summary_cloud_model": None,
        "deepinfra_api_key_set": False,
    }


async def test_non_admin_user_cannot_manage_model_settings(make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        created = await client.post(
            "/services/jdr/users",
            json={"username": "alice", "system_role": "user", "password": "secret"},
        )
        assert created.status_code == 201
        login = await client.post(
            "/services/jdr/auth/login",
            json={"username": "alice", "password": "secret"},
        )
        assert login.status_code == 200
        response = await client.get("/services/jdr/settings/models")

    assert response.status_code == 403


async def test_model_settings_are_scoped_to_web_user(make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        created = await client.post(
            "/services/jdr/users",
            json={"username": "other", "system_role": "admin", "password": "secret"},
        )
        assert created.status_code == 201
        first_patch = await client.patch(
            "/services/jdr/settings/models",
            json={"transcription_provider": "local"},
        )
        assert first_patch.status_code == 200

        other_login = await client.post(
            "/services/jdr/auth/login",
            json={"username": "other", "password": "secret"},
        )
        assert other_login.status_code == 200
        other_settings = await client.get("/services/jdr/settings/models")

    assert other_settings.status_code == 200
    assert other_settings.json() == {
        "transcription_provider": "cloud",
        "summary_provider": "cloud",
        "transcription_local_path": None,
        "summary_local_path": None,
        "transcription_cloud_model": None,
        "summary_cloud_model": None,
        "deepinfra_api_key_set": False,
    }


async def test_invalid_model_provider_is_rejected(make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        response = await client.patch(
            "/services/jdr/settings/models",
            json={"transcription_provider": "deepinfra"},
        )

    assert response.status_code == 422


async def test_admin_can_persist_cloud_models(make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        patched = await client.patch(
            "/services/jdr/settings/models",
            json={
                "transcription_cloud_model": "openai/whisper-large-v3-turbo",
                "summary_cloud_model": "Qwen/Qwen2.5-72B-Instruct",
            },
        )
        fetched = await client.get("/services/jdr/settings/models")

    assert patched.status_code == 200
    assert patched.json() == {
        "transcription_provider": "cloud",
        "summary_provider": "cloud",
        "transcription_local_path": None,
        "summary_local_path": None,
        "transcription_cloud_model": "openai/whisper-large-v3-turbo",
        "summary_cloud_model": "Qwen/Qwen2.5-72B-Instruct",
        "deepinfra_api_key_set": False,
    }
    assert fetched.json() == patched.json()


async def test_deepinfra_api_key_is_write_only(make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        patched = await client.patch(
            "/services/jdr/settings/models",
            json={"deepinfra_api_key": "di-secret-key"},
        )
        fetched = await client.get("/services/jdr/settings/models")

    assert patched.status_code == 200
    # The raw key is never echoed back; only the boolean indicator flips to True.
    assert "deepinfra_api_key" not in patched.json()
    assert patched.json()["deepinfra_api_key_set"] is True
    assert fetched.json()["deepinfra_api_key_set"] is True
    assert "deepinfra_api_key" not in fetched.json()


async def test_empty_deepinfra_api_key_keeps_existing(make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        await client.patch(
            "/services/jdr/settings/models",
            json={"deepinfra_api_key": "di-secret-key"},
        )
        # An empty/omitted key must not clear the stored credential.
        followup = await client.patch(
            "/services/jdr/settings/models",
            json={"deepinfra_api_key": "", "summary_provider": "ollama"},
        )

    assert followup.status_code == 200
    assert followup.json()["deepinfra_api_key_set"] is True
    assert followup.json()["summary_provider"] == "ollama"
