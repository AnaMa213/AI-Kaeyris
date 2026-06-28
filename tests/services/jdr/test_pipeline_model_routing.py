"""BD-19: per-user model settings routing for JDR jobs."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.adapters.llm import LocalLLMAdapter, MockLLMAdapter, OpenAICompatibleLLMAdapter
from app.adapters.transcription import (
    LocalFasterWhisperTranscriptionAdapter,
    MockTranscriptionAdapter,
    OpenAICompatibleTranscriptionAdapter,
)
from app.jobs import PermanentJobError
from app.core.models import SystemRole, User
from app.jobs.jdr import (
    _build_llm_adapter_for_user,
    _build_transcription_adapter_for_user,
    _resolve_session_owner_id,
)
from app.services.jdr.db.models import (
    ApiKey,
    Campaign,
    ModelProvider,
    ModelSettings,
    Role,
    Session,
    SessionMode,
    SessionState,
    TranscriptionMode,
)


def _settings_row(
    *,
    summary_provider: ModelProvider = ModelProvider.CLOUD,
    transcription_provider: ModelProvider = ModelProvider.CLOUD,
    deepinfra_api_key: str | None = None,
    summary_cloud_model: str | None = None,
    transcription_cloud_model: str | None = None,
    summary_local_path: str | None = None,
    transcription_local_path: str | None = None,
    summary_local_validation_hash: str | None = None,
    transcription_local_validation_hash: str | None = None,
    ollama_model: str | None = None,
) -> ModelSettings:
    return ModelSettings(
        user_id=uuid4(),
        summary_provider=summary_provider,
        transcription_provider=transcription_provider,
        deepinfra_api_key=deepinfra_api_key,
        summary_cloud_model=summary_cloud_model,
        transcription_cloud_model=transcription_cloud_model,
        summary_local_path=summary_local_path,
        transcription_local_path=transcription_local_path,
        summary_local_validation_hash=summary_local_validation_hash,
        transcription_local_validation_hash=transcription_local_validation_hash,
        ollama_model=ollama_model,
    )


def test_llm_adapter_env_fallback(monkeypatch):
    monkeypatch.setattr("app.adapters.llm.settings.LLM_PROVIDER", "mock")

    adapter = _build_llm_adapter_for_user(None)

    assert isinstance(adapter, MockLLMAdapter)


def test_llm_adapter_none_provider_uses_env_fallback(monkeypatch):
    # Story 7.2 / BD-22: a NULL summary_provider means "inherit operator default".
    monkeypatch.setattr("app.adapters.llm.settings.LLM_PROVIDER", "mock")
    row = _settings_row(summary_provider=None)

    adapter = _build_llm_adapter_for_user(row)

    assert isinstance(adapter, MockLLMAdapter)


def test_transcription_adapter_none_provider_uses_env_fallback(monkeypatch):
    # Story 7.2 / BD-22: a NULL transcription_provider means "inherit operator
    # default" — e.g. the operator's env-configured local model — instead of
    # being forced to cloud.
    monkeypatch.setattr(
        "app.adapters.transcription.settings.TRANSCRIPTION_PROVIDER", "mock"
    )
    row = _settings_row(transcription_provider=None)

    adapter = _build_transcription_adapter_for_user(row)

    assert isinstance(adapter, MockTranscriptionAdapter)


def test_llm_adapter_cloud_paid(monkeypatch):
    monkeypatch.setattr("app.adapters.llm.settings.LLM_MODEL", "env-model")
    row = _settings_row(
        deepinfra_api_key="personal-key",
        summary_cloud_model="Qwen/Qwen2.5-72B-Instruct",
    )

    adapter = _build_llm_adapter_for_user(row)

    assert isinstance(adapter, OpenAICompatibleLLMAdapter)
    assert adapter.provider == "deepinfra"
    assert adapter.model == "Qwen/Qwen2.5-72B-Instruct"
    assert adapter.base_url == "https://api.deepinfra.com/v1/openai"


def test_llm_adapter_cloud_free(monkeypatch):
    monkeypatch.setattr("app.adapters.llm.settings.LLM_PROVIDER", "mock")
    row = _settings_row()

    adapter = _build_llm_adapter_for_user(row)

    assert isinstance(adapter, MockLLMAdapter)


def test_llm_adapter_ollama(monkeypatch):
    monkeypatch.setattr("app.adapters.llm.settings.LLM_BASE_URL", "")
    row = _settings_row(
        summary_provider=ModelProvider.OLLAMA,
        ollama_model="llama3:8b",
    )

    adapter = _build_llm_adapter_for_user(row)

    assert isinstance(adapter, OpenAICompatibleLLMAdapter)
    assert adapter.provider == "ollama"
    assert adapter.model == "llama3:8b"


def test_llm_adapter_local_uses_validated_path():
    row = _settings_row(
        summary_provider=ModelProvider.LOCAL,
        summary_local_path="/models/mistral.gguf",
        summary_local_validation_hash="proof-hash",
    )

    adapter = _build_llm_adapter_for_user(row)

    assert isinstance(adapter, LocalLLMAdapter)
    assert adapter.provider == "local"
    assert adapter.model_path == "/models/mistral.gguf"


def test_llm_adapter_local_without_validated_path_fails():
    row = _settings_row(summary_provider=ModelProvider.LOCAL)

    try:
        _build_llm_adapter_for_user(row)
    except PermanentJobError as exc:
        assert "validated local summary" in str(exc).lower()
    else:
        raise AssertionError("Local summary without proof must fail visibly.")


def test_transcription_adapter_cloud_paid():
    row = _settings_row(
        deepinfra_api_key="personal-key",
        transcription_cloud_model="openai/whisper-large-v3-turbo",
    )

    adapter = _build_transcription_adapter_for_user(row)

    assert isinstance(adapter, OpenAICompatibleTranscriptionAdapter)
    assert adapter.provider == "cloud"
    assert adapter.model == "openai/whisper-large-v3-turbo"
    assert adapter.base_url == "https://api.deepinfra.com/v1/openai"


def test_transcription_adapter_cloud_free(monkeypatch):
    monkeypatch.setattr(
        "app.adapters.transcription.settings.TRANSCRIPTION_PROVIDER", "mock"
    )
    row = _settings_row()

    adapter = _build_transcription_adapter_for_user(row)

    assert isinstance(adapter, MockTranscriptionAdapter)


def test_transcription_adapter_local_uses_validated_path():
    row = _settings_row(
        transcription_provider=ModelProvider.LOCAL,
        transcription_local_path="/models/whisper-large-v3",
        transcription_local_validation_hash="proof-hash",
    )

    adapter = _build_transcription_adapter_for_user(row)

    assert isinstance(adapter, LocalFasterWhisperTranscriptionAdapter)
    assert adapter.provider == "local"
    assert adapter.model_path == "/models/whisper-large-v3"


def test_transcription_adapter_local_without_validated_path_fails():
    row = _settings_row(transcription_provider=ModelProvider.LOCAL)

    try:
        _build_transcription_adapter_for_user(row)
    except PermanentJobError as exc:
        assert "validated local transcription" in str(exc).lower()
    else:
        raise AssertionError("Local transcription without proof must fail visibly.")


def _gm_key(name: str = "gm-key") -> ApiKey:
    return ApiKey(
        name=name,
        hash="argon2-hash",
        role=Role.GM,
    )


def _user(username: str, api_key_id: UUID | None = None) -> User:
    return User(
        username=username,
        system_role=SystemRole.ADMIN,
        password_hash="argon2-password",
        api_key_id=api_key_id,
    )


def _session(gm_key_id: UUID, campaign_id: UUID | None = None) -> Session:
    return Session(
        title="Routing session",
        recorded_at=datetime(2026, 6, 16, tzinfo=UTC),
        gm_key_id=gm_key_id,
        campaign_id=campaign_id,
        mode=SessionMode.BATCH,
        state=SessionState.TRANSCRIBED,
        transcription_mode=TranscriptionMode.DIARISED,
    )


async def test_owner_resolution_via_campaign(db_engine, monkeypatch):
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)

    async with sm() as db:
        api_key = _gm_key()
        db.add(api_key)
        await db.flush()
        owner = _user("campaign-owner", api_key.id)
        db.add(owner)
        await db.flush()
        campaign = Campaign(name="Owner campaign", owner_user_id=owner.id)
        db.add(campaign)
        await db.flush()
        session = _session(api_key.id, campaign.id)
        db.add(session)
        await db.commit()
        session_id = session.id
        owner_id = owner.id

    assert await _resolve_session_owner_id(session_id) == owner_id


async def test_owner_resolution_via_gm_key(db_engine, monkeypatch):
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)

    async with sm() as db:
        api_key = _gm_key()
        db.add(api_key)
        await db.flush()
        owner = _user("key-owner", api_key.id)
        db.add(owner)
        await db.flush()
        session = _session(api_key.id)
        db.add(session)
        await db.commit()
        session_id = session.id
        owner_id = owner.id

    assert await _resolve_session_owner_id(session_id) == owner_id


async def test_owner_resolution_none(db_engine, monkeypatch):
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.jobs.jdr.get_sessionmaker", lambda: sm)

    async with sm() as db:
        api_key = _gm_key()
        db.add(api_key)
        await db.flush()
        session = _session(api_key.id)
        db.add(session)
        await db.commit()
        session_id = session.id

    assert await _resolve_session_owner_id(session_id) is None
