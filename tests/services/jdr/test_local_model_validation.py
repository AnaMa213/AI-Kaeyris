from collections.abc import Callable

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.adapters.local_models import LocalModelProbeError, LocalModelProbeResult
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


async def test_admin_can_validate_local_transcription_model(
    make_db_session_dep,
    monkeypatch,
    tmp_path,
):
    async def fake_probe(*, category, model_path, timeout_seconds=None):
        assert category == "transcription"
        assert model_path == str(tmp_path.resolve())
        assert timeout_seconds is not None
        return LocalModelProbeResult(
            runtime="fake-whisper",
            model_format="fake-whisper-format",
            message="Accepted.",
        )

    monkeypatch.setattr(
        "app.services.jdr.local_model_validation.probe_local_model",
        fake_probe,
    )
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        response = await client.post(
            "/services/jdr/settings/models/local/validation",
            json={"category": "transcription", "model_path": str(tmp_path)},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["validation_id"]
    assert payload["category"] == "transcription"
    assert payload["model_path"] == str(tmp_path.resolve())
    assert payload["status"] == "succeeded"
    assert payload["runtime"] == "fake-whisper"
    assert payload["model_format"] == "fake-whisper-format"
    assert payload["message"] == "Accepted."
    assert payload["expires_at"].endswith("Z")


async def test_validation_failure_returns_problem_details(
    make_db_session_dep,
    monkeypatch,
    tmp_path,
):
    async def fake_probe(*, category, model_path, timeout_seconds=None):
        _ = (category, model_path, timeout_seconds)
        raise LocalModelProbeError(
            "local-model-timeout",
            "Local model validation timed out",
            "The local model did not load within the configured validation budget.",
        )

    monkeypatch.setattr(
        "app.services.jdr.local_model_validation.probe_local_model",
        fake_probe,
    )
    app = _make_app(make_db_session_dep)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _setup_admin(client)
        response = await client.post(
            "/services/jdr/settings/models/local/validation",
            json={"category": "summary", "model_path": str(tmp_path / "m.gguf")},
        )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    payload = response.json()
    assert payload["type"].endswith("/local-model-timeout")
    assert "Traceback" not in str(payload)


async def test_non_admin_cannot_validate_local_model(make_db_session_dep, tmp_path):
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
        response = await client.post(
            "/services/jdr/settings/models/local/validation",
            json={"category": "summary", "model_path": str(tmp_path / "m.gguf")},
        )

    assert response.status_code == 403
