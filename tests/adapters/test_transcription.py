"""Tests for the TranscriptionAdapter Protocol, factory and error mapping."""

from typing import cast
from unittest.mock import AsyncMock, mock_open, patch

import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)

from app.adapters.transcription import (
    MockTranscriptionAdapter,
    OpenAICompatibleTranscriptionAdapter,
    PermanentTranscriptionError,
    TranscriptionAdapter,
    TranscriptionResult,
    TranscriptionSegment,
    TransientTranscriptionError,
    build_transcription_adapter,
    get_transcription_adapter,
)


# ---- Mock adapter ---------------------------------------------------------


async def test_mock_adapter_returns_three_deterministic_segments():
    adapter: TranscriptionAdapter = MockTranscriptionAdapter()
    result = await adapter.transcribe(audio_path="anywhere.m4a")

    assert isinstance(result, TranscriptionResult)
    assert result.provider == "mock"
    assert result.model_used == "mock:whisper"
    assert len(result.segments) == 3
    assert result.segments[0] == TranscriptionSegment(
        "speaker_1", 0.0, 1.5, "[mock] segment one"
    )
    assert result.segments[1].speaker_label == "speaker_2"


async def test_mock_adapter_uses_language_hint_when_provided():
    adapter = MockTranscriptionAdapter()
    result = await adapter.transcribe(audio_path="x.m4a", language_hint="en")
    assert result.language == "en"


# ---- Factory --------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_adapter_cache():
    get_transcription_adapter.cache_clear()
    yield
    get_transcription_adapter.cache_clear()


def test_build_factory_returns_mock_for_mock_provider(monkeypatch):
    monkeypatch.setattr(
        "app.adapters.transcription.settings.TRANSCRIPTION_PROVIDER", "mock"
    )
    adapter = build_transcription_adapter()
    assert isinstance(adapter, MockTranscriptionAdapter)


def test_build_factory_rejects_unknown_provider(monkeypatch):
    monkeypatch.setattr(
        "app.adapters.transcription.settings.TRANSCRIPTION_PROVIDER", "wat"
    )
    with pytest.raises(RuntimeError, match="Unknown TRANSCRIPTION_PROVIDER"):
        build_transcription_adapter()


def test_build_factory_requires_api_key_for_cloud(monkeypatch):
    monkeypatch.setattr(
        "app.adapters.transcription.settings.TRANSCRIPTION_PROVIDER", "cloud"
    )
    monkeypatch.setattr(
        "app.adapters.transcription.settings.TRANSCRIPTION_API_KEY", ""
    )
    with pytest.raises(RuntimeError, match="TRANSCRIPTION_API_KEY"):
        build_transcription_adapter()


def test_build_factory_requires_base_url_for_local(monkeypatch):
    monkeypatch.setattr(
        "app.adapters.transcription.settings.TRANSCRIPTION_PROVIDER", "local"
    )
    monkeypatch.setattr(
        "app.adapters.transcription.settings.TRANSCRIPTION_BASE_URL", ""
    )
    with pytest.raises(RuntimeError, match="TRANSCRIPTION_BASE_URL"):
        build_transcription_adapter()


def test_build_factory_builds_cloud_adapter_with_api_key(monkeypatch):
    monkeypatch.setattr(
        "app.adapters.transcription.settings.TRANSCRIPTION_PROVIDER", "cloud"
    )
    monkeypatch.setattr(
        "app.adapters.transcription.settings.TRANSCRIPTION_API_KEY", "sk-test"
    )
    adapter = build_transcription_adapter()
    assert isinstance(adapter, OpenAICompatibleTranscriptionAdapter)
    assert adapter.provider == "cloud"


def test_get_transcription_adapter_caches(monkeypatch):
    monkeypatch.setattr(
        "app.adapters.transcription.settings.TRANSCRIPTION_PROVIDER", "mock"
    )
    a = get_transcription_adapter()
    b = get_transcription_adapter()
    assert a is b


# ---- Error mapping (OpenAICompatibleTranscriptionAdapter) ----------------


def _adapter_with_failing_client(
    exc_to_raise: Exception,
) -> OpenAICompatibleTranscriptionAdapter:
    """Build an adapter whose underlying SDK raises ``exc_to_raise``."""
    adapter = OpenAICompatibleTranscriptionAdapter(
        provider="cloud",
        model="whisper-large-v3",
        api_key="dummy",
        base_url=None,
    )
    failing = AsyncMock(side_effect=exc_to_raise)
    adapter._client.audio.transcriptions.create = failing  # type: ignore[method-assign]
    return adapter


def _make_status_exc(cls, status_code: int) -> Exception:
    """Build an OpenAI status-error instance bypassing the Response check."""
    exc = cls.__new__(cls)
    exc.status_code = status_code
    exc.message = f"synthetic {status_code}"
    exc.body = None
    exc.response = cast(object, None)
    exc.request_id = None
    Exception.__init__(exc, exc.message)
    return exc


@pytest.mark.parametrize(
    "exc",
    [
        APITimeoutError(request=cast(object, None)),  # type: ignore[arg-type]
        APIConnectionError(request=cast(object, None)),  # type: ignore[arg-type]
        _make_status_exc(RateLimitError, 429),
        _make_status_exc(InternalServerError, 500),
    ],
)
async def test_transient_errors_are_remapped(exc):
    adapter = _adapter_with_failing_client(exc)
    with patch("builtins.open", mock_open(read_data=b"fake-audio")):
        with pytest.raises(TransientTranscriptionError):
            await adapter.transcribe(audio_path="x.m4a")


@pytest.mark.parametrize(
    "exc",
    [
        _make_status_exc(AuthenticationError, 401),
        _make_status_exc(PermissionDeniedError, 403),
        _make_status_exc(BadRequestError, 400),
        _make_status_exc(UnprocessableEntityError, 422),
    ],
)
async def test_permanent_errors_are_remapped(exc):
    adapter = _adapter_with_failing_client(exc)
    with patch("builtins.open", mock_open(read_data=b"fake-audio")):
        with pytest.raises(PermanentTranscriptionError):
            await adapter.transcribe(audio_path="x.m4a")


async def test_adapter_passes_vad_filter_and_anti_loop_options():
    """Speaches/faster-whisper hallucinates on silence without VAD.
    We must always pass vad_filter=True and condition_on_previous_text=False
    via extra_body to combat the well-known repetition-loop failure mode."""
    adapter = OpenAICompatibleTranscriptionAdapter(
        provider="local",
        model="Systran/faster-whisper-large-v3",
        api_key="dummy",
        base_url="http://localhost:8005/v1",
    )

    captured: dict[str, object] = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        # Minimal verbose_json shape so the adapter doesn't crash on the
        # rest of the pipeline.
        from types import SimpleNamespace
        return SimpleNamespace(language="fr", segments=[])

    adapter._client.audio.transcriptions.create = _capture  # type: ignore[method-assign]

    with patch("builtins.open", mock_open(read_data=b"fake-audio")):
        await adapter.transcribe(audio_path="x.m4a", language_hint="fr")

    assert "extra_body" in captured
    extra = captured["extra_body"]
    assert isinstance(extra, dict)
    assert extra["vad_filter"] is True
    assert extra["condition_on_previous_text"] is False
    # temperature must be deterministic for transcription
    assert captured["temperature"] == 0


async def test_missing_audio_file_is_permanent_error():
    """OSError on file open must surface as a PermanentTranscriptionError."""
    adapter = OpenAICompatibleTranscriptionAdapter(
        provider="cloud",
        model="whisper-large-v3",
        api_key="dummy",
    )
    with pytest.raises(PermanentTranscriptionError, match="Cannot read audio file"):
        await adapter.transcribe(audio_path="/nonexistent/path/to/audio.m4a")


# ---- Segment extraction --------------------------------------------------


def test_extract_segments_handles_dict_form():
    """Local LAN endpoint returns dict-shaped segments with a 'speaker' field."""
    from app.adapters.transcription import _extract_segments

    class FakeResp:
        segments = [
            {
                "speaker": "speaker_1",
                "start": 0.0,
                "end": 2.5,
                "text": " Bonjour à tous ",
            },
            {
                "speaker": "speaker_2",
                "start": 2.5,
                "end": 5.0,
                "text": "Salut",
            },
        ]

    out = _extract_segments(FakeResp())
    assert len(out) == 2
    assert out[0].speaker_label == "speaker_1"
    assert out[0].text == "Bonjour à tous"  # stripped
    assert out[1].speaker_label == "speaker_2"


def test_extract_segments_falls_back_to_unknown_when_no_speaker():
    """Cloud OpenAI Whisper API returns segments without 'speaker'."""
    from app.adapters.transcription import _extract_segments

    class FakeSegment:
        # Pydantic-like: attributes, not dict keys.
        start = 0.0
        end = 1.5
        text = "hello"

    class FakeResp:
        segments = [FakeSegment()]

    out = _extract_segments(FakeResp())
    assert len(out) == 1
    assert out[0].speaker_label == "unknown"


def test_extract_segments_handles_empty_response():
    from app.adapters.transcription import _extract_segments

    class FakeResp:
        segments = None

    assert _extract_segments(FakeResp()) == []
