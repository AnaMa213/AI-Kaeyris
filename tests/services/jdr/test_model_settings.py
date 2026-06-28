"""AI model settings endpoint tests (BD-18 model config)."""

from collections.abc import Callable

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.adapters.local_models import LocalModelProbeResult
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


def _mock_local_probe(monkeypatch) -> None:
    async def fake_probe(*, category, model_path, timeout_seconds=None):
        _ = timeout_seconds
        return LocalModelProbeResult(
            runtime=f"fake-{category}",
            model_format=f"fake-{category}-format",
            message="Accepted.",
        )

    monkeypatch.setattr(
        "app.services.jdr.local_model_validation.probe_local_model",
        fake_probe,
    )


async def _validate_local_model(
    client: AsyncClient,
    *,
    category: str,
    model_path: str,
) -> str:
    response = await client.post(
        "/services/jdr/settings/models/local/validation",
        json={"category": category, "model_path": model_path},
    )
    assert response.status_code == 200
    return str(response.json()["validation_id"])


def _mock_cloud_key_probe(monkeypatch, *, ok: bool) -> None:
    """Stub the save-time DeepInfra key probe (Story 7.4 / B) — no network."""
    from app.adapters.llm import PermanentLLMError

    class _FakeCloudAdapter:
        provider = "deepinfra"
        model = "stub"

        async def complete(self, *, system, user, max_tokens):
            if ok:
                return "ok"
            raise PermanentLLMError("AuthenticationError: invalid_api_key")

    monkeypatch.setattr(
        "app.services.jdr.auth_router.build_personal_cloud_llm_adapter",
        lambda *, model, api_key: _FakeCloudAdapter(),
    )


def _set_operator_cloud_defaults(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.jdr.auth_router.settings.TRANSCRIPTION_PROVIDER", "cloud"
    )
    monkeypatch.setattr(
        "app.services.jdr.auth_router.settings.TRANSCRIPTION_MODEL",
        "whisper-large-v3",
    )
    monkeypatch.setattr(
        "app.services.jdr.auth_router.settings.LLM_PROVIDER", "deepinfra"
    )
    monkeypatch.setattr(
        "app.services.jdr.auth_router.settings.LLM_MODEL",
        "meta-llama/Meta-Llama-3.1-8B-Instruct",
    )


async def test_admin_gets_default_model_settings(make_db_session_dep, monkeypatch):
    _set_operator_cloud_defaults(monkeypatch)
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
        "transcription_cloud_model": "whisper-large-v3",
        "summary_cloud_model": "meta-llama/Meta-Llama-3.1-8B-Instruct",
        "ollama_model": None,
        "deepinfra_api_key_set": False,
    }


async def test_admin_gets_effective_ollama_defaults(
    make_db_session_dep, monkeypatch
):
    monkeypatch.setattr("app.services.jdr.auth_router.settings.LLM_PROVIDER", "ollama")
    monkeypatch.setattr("app.services.jdr.auth_router.settings.LLM_MODEL", "llama3:8b")
    monkeypatch.setattr(
        "app.services.jdr.auth_router.settings.TRANSCRIPTION_PROVIDER", "cloud"
    )
    monkeypatch.setattr(
        "app.services.jdr.auth_router.settings.TRANSCRIPTION_MODEL",
        "whisper-large-v3-turbo",
    )
    monkeypatch.setattr(
        "app.services.jdr.auth_router.settings.LLM_API_KEY", "operator-secret"
    )

    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        response = await client.get("/services/jdr/settings/models")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "transcription_provider": "cloud",
        "summary_provider": "ollama",
        "transcription_local_path": None,
        "summary_local_path": None,
        "transcription_cloud_model": "whisper-large-v3-turbo",
        "summary_cloud_model": None,
        "ollama_model": "llama3:8b",
        "deepinfra_api_key_set": False,
    }
    assert "deepinfra_api_key" not in payload
    assert "operator-secret" not in str(payload)


async def test_admin_can_persist_model_settings(make_db_session_dep, monkeypatch):
    # Pin operator defaults so the test is deterministic regardless of the local
    # .env (Story 7.2: an unset provider now resolves to the effective default).
    _set_operator_cloud_defaults(monkeypatch)
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        patched = await client.patch(
            "/services/jdr/settings/models",
            json={
                "summary_provider": "ollama",
            },
        )
        fetched = await client.get("/services/jdr/settings/models")

    assert patched.status_code == 200
    # transcription_provider was NOT in the PATCH → it stays NULL = inherit the
    # operator default (cloud), with the effective cloud model surfaced.
    assert patched.json() == {
        "transcription_provider": "cloud",
        "summary_provider": "ollama",
        "transcription_local_path": None,
        "summary_local_path": None,
        "transcription_cloud_model": "whisper-large-v3",
        "summary_cloud_model": None,
        "ollama_model": "meta-llama/Meta-Llama-3.1-8B-Instruct",
        "deepinfra_api_key_set": False,
    }
    assert fetched.status_code == 200
    assert fetched.json() == patched.json()


async def test_admin_can_persist_local_model_paths(
    make_db_session_dep,
    monkeypatch,
    tmp_path,
):
    _mock_local_probe(monkeypatch)
    _set_operator_cloud_defaults(monkeypatch)
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    model_path = str(tmp_path / "whisper-large-v3")

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        validation_id = await _validate_local_model(
            client,
            category="transcription",
            model_path=model_path,
        )
        patched = await client.patch(
            "/services/jdr/settings/models",
            json={
                "transcription_provider": "local",
                "transcription_local_path": model_path,
                "transcription_local_validation_id": validation_id,
            },
        )
        fetched = await client.get("/services/jdr/settings/models")

    assert patched.status_code == 200
    # summary_provider was not patched → NULL = inherit operator default (cloud),
    # surfacing the effective cloud model.
    assert patched.json() == {
        "transcription_provider": "local",
        "summary_provider": "cloud",
        "transcription_local_path": model_path,
        "summary_local_path": None,
        "transcription_cloud_model": None,
        "summary_cloud_model": "meta-llama/Meta-Llama-3.1-8B-Instruct",
        "ollama_model": None,
        "deepinfra_api_key_set": False,
    }
    assert fetched.status_code == 200
    assert fetched.json() == patched.json()


async def test_patch_model_settings_is_partial(
    make_db_session_dep,
    monkeypatch,
    tmp_path,
):
    _mock_local_probe(monkeypatch)
    _set_operator_cloud_defaults(monkeypatch)
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    model_path = str(tmp_path / "whisper-large-v3")

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        validation_id = await _validate_local_model(
            client,
            category="transcription",
            model_path=model_path,
        )
        first = await client.patch(
            "/services/jdr/settings/models",
            json={
                "transcription_provider": "local",
                "summary_provider": "ollama",
                "transcription_local_path": model_path,
                "transcription_local_validation_id": validation_id,
            },
        )
        second = await client.patch(
            "/services/jdr/settings/models",
            json={"summary_provider": "cloud"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    # transcription stays explicitly local (set in the first PATCH); summary was
    # switched to cloud → surfaces the effective cloud model.
    assert second.json() == {
        "transcription_provider": "local",
        "summary_provider": "cloud",
        "transcription_local_path": model_path,
        "summary_local_path": None,
        "transcription_cloud_model": None,
        "summary_cloud_model": "meta-llama/Meta-Llama-3.1-8B-Instruct",
        "ollama_model": None,
        "deepinfra_api_key_set": False,
    }


async def test_first_patch_keeps_effective_local_transcription(
    make_db_session_dep, monkeypatch
):
    """Story 7.2 / BD-22 — régression : avec un défaut opérateur Transcription=Local,
    basculer SEULEMENT le résumé en Cloud ne doit PAS faire basculer la
    transcription en Cloud.

    Le champ provider absent du PATCH reste NULL (= hériter du défaut effectif)
    au lieu de retomber sur l'ancien défaut de colonne `cloud`.
    """
    monkeypatch.setattr(
        "app.services.jdr.auth_router.settings.TRANSCRIPTION_PROVIDER", "local"
    )
    monkeypatch.setattr(
        "app.services.jdr.auth_router.settings.LLM_PROVIDER", "deepinfra"
    )
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        # Avant tout enregistrement, le GET reflète déjà le défaut effectif Local.
        before = await client.get("/services/jdr/settings/models")
        assert before.json()["transcription_provider"] == "local"

        patched = await client.patch(
            "/services/jdr/settings/models",
            json={"summary_provider": "cloud"},
        )
        fetched = await client.get("/services/jdr/settings/models")

    assert patched.status_code == 200
    assert patched.json()["transcription_provider"] == "local"
    assert patched.json()["summary_provider"] == "cloud"
    assert fetched.json()["transcription_provider"] == "local"
    assert fetched.json()["summary_provider"] == "cloud"


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


async def test_model_settings_are_scoped_to_web_user(
    make_db_session_dep, monkeypatch
):
    _set_operator_cloud_defaults(monkeypatch)
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
            json={"summary_provider": "ollama"},
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
        "transcription_cloud_model": "whisper-large-v3",
        "summary_cloud_model": "meta-llama/Meta-Llama-3.1-8B-Instruct",
        "ollama_model": None,
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


async def test_admin_can_persist_cloud_models(make_db_session_dep, monkeypatch):
    # Providers stay NULL here (only cloud models are patched) → they resolve to
    # the pinned cloud operator default; the stored cloud models are preferred.
    _set_operator_cloud_defaults(monkeypatch)
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
        "ollama_model": None,
        "deepinfra_api_key_set": False,
    }
    assert fetched.json() == patched.json()


async def test_invalid_cloud_key_rejected_on_save(make_db_session_dep, monkeypatch):
    # Story 7.4 / B — a bad DeepInfra key fails the save-time probe → 400, not stored.
    _mock_cloud_key_probe(monkeypatch, ok=False)
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        response = await client.patch(
            "/services/jdr/settings/models",
            json={
                "summary_provider": "cloud",
                "summary_cloud_model": "meta-llama/Meta-Llama-3.1-8B-Instruct",
                "deepinfra_api_key": "bad-key",
            },
        )
        fetched = await client.get("/services/jdr/settings/models")

    assert response.status_code == 400
    assert response.json()["type"].endswith("/cloud-api-key-invalid")
    # The bad key was NOT persisted.
    assert fetched.json()["deepinfra_api_key_set"] is False


async def test_valid_cloud_key_passes_probe_and_is_stored(
    make_db_session_dep, monkeypatch
):
    # Story 7.4 / B — a valid key passes the probe and is stored as before.
    _mock_cloud_key_probe(monkeypatch, ok=True)
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        response = await client.patch(
            "/services/jdr/settings/models",
            json={
                "summary_provider": "cloud",
                "summary_cloud_model": "meta-llama/Meta-Llama-3.1-8B-Instruct",
                "deepinfra_api_key": "good-key",
            },
        )
        fetched = await client.get("/services/jdr/settings/models")

    assert response.status_code == 200
    assert fetched.json()["deepinfra_api_key_set"] is True
    assert fetched.json()["summary_provider"] == "cloud"


async def test_local_path_without_validation_is_rejected(
    make_db_session_dep,
    tmp_path,
):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        response = await client.patch(
            "/services/jdr/settings/models",
            json={
                "transcription_provider": "local",
                "transcription_local_path": str(tmp_path / "whisper"),
            },
        )

    assert response.status_code == 400
    assert response.json()["type"].endswith("/local-model-validation-required")


async def test_local_path_with_wrong_category_proof_is_rejected(
    make_db_session_dep,
    monkeypatch,
    tmp_path,
):
    _mock_local_probe(monkeypatch)
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    model_path = str(tmp_path / "whisper")

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        validation_id = await _validate_local_model(
            client,
            category="summary",
            model_path=model_path,
        )
        response = await client.patch(
            "/services/jdr/settings/models",
            json={
                "transcription_provider": "local",
                "transcription_local_path": model_path,
                "transcription_local_validation_id": validation_id,
            },
        )

    assert response.status_code == 400
    assert response.json()["type"].endswith("/local-model-validation-required")


async def test_local_path_with_wrong_path_proof_is_rejected(
    make_db_session_dep,
    monkeypatch,
    tmp_path,
):
    _mock_local_probe(monkeypatch)
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        validation_id = await _validate_local_model(
            client,
            category="transcription",
            model_path=str(tmp_path / "whisper-a"),
        )
        response = await client.patch(
            "/services/jdr/settings/models",
            json={
                "transcription_provider": "local",
                "transcription_local_path": str(tmp_path / "whisper-b"),
                "transcription_local_validation_id": validation_id,
            },
        )

    assert response.status_code == 400
    assert response.json()["type"].endswith("/local-model-validation-required")


async def test_expired_local_validation_is_rejected(
    make_db_session_dep,
    monkeypatch,
    tmp_path,
):
    _mock_local_probe(monkeypatch)
    monkeypatch.setattr(
        "app.services.jdr.local_model_validation.settings.LOCAL_MODEL_VALIDATION_TTL_SECONDS",
        -1,
    )
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)
    model_path = str(tmp_path / "whisper")

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        validation_id = await _validate_local_model(
            client,
            category="transcription",
            model_path=model_path,
        )
        response = await client.patch(
            "/services/jdr/settings/models",
            json={
                "transcription_provider": "local",
                "transcription_local_path": model_path,
                "transcription_local_validation_id": validation_id,
            },
        )

    assert response.status_code == 400
    assert response.json()["type"].endswith("/local-model-validation-expired")


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


async def test_admin_can_persist_ollama_model(make_db_session_dep):
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        patched = await client.patch(
            "/services/jdr/settings/models",
            json={
                "summary_provider": "ollama",
                "ollama_model": "llama3:8b",
            },
        )
        fetched = await client.get("/services/jdr/settings/models")

    assert patched.status_code == 200
    assert patched.json()["summary_provider"] == "ollama"
    assert patched.json()["ollama_model"] == "llama3:8b"
    assert "deepinfra_api_key" not in patched.json()
    assert fetched.json()["ollama_model"] == "llama3:8b"
