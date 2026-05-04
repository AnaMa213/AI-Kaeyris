from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_VERSION: str = "0.0.1"

    # Authentication — see ADR 0003.
    # Format: "name1:argon2_hash1;name2:argon2_hash2" (semicolon separator).
    # Empty by default → all authenticated routes reject every request.
    API_KEYS: str = ""

    # Async jobs and rate limiting — see ADR 0004.
    REDIS_URL: str = "redis://localhost:6379/0"
    RATE_LIMIT_PER_MINUTE: int = 60
    RATE_LIMIT_WINDOW_SECONDS: int = 60

    # LLM adapter — see ADR 0005.
    # Supported providers (all use the OpenAI-compatible SDK): deepinfra,
    # openai, groq, ollama, vllm, together, mock.
    LLM_PROVIDER: str = "deepinfra"
    LLM_MODEL: str = "meta-llama/Meta-Llama-3.1-8B-Instruct"
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = ""
    LLM_TIMEOUT_SECONDS: float = 60.0
    LLM_MAX_TOKENS_DEFAULT: int = 1000

    # Persistence — see ADR 0006.
    # SQLite in dev, Postgres in prod (Jalon 8). The async driver is
    # implied by the URL scheme (sqlite+aiosqlite, postgresql+asyncpg).
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/kaeyris.db"

    # JDR service — see ADR 0006.
    # Local data directory: audios while being transcribed, then purged
    # (FR-004), and the SQLite file when DATABASE_URL points there.
    KAEYRIS_DATA_DIR: str = "./data"

    # Transcription adapter — see ADR 0006 §2 and the spec contract
    # `specs/001-kaeyris-jdr/contracts/transcription-adapter.md`.
    # Providers: cloud (OpenAI/Groq/DeepInfra/Together) or local
    # (self-hosted faster-whisper + pyannote behind an OpenAI-compatible
    # base_url on a LAN GPU host). 'mock' is reserved for tests.
    TRANSCRIPTION_PROVIDER: str = "cloud"
    TRANSCRIPTION_BASE_URL: str = ""
    TRANSCRIPTION_API_KEY: str = ""
    TRANSCRIPTION_MODEL: str = "whisper-large-v3"
    TRANSCRIPTION_TIMEOUT_SECONDS: float = 1800.0
    TRANSCRIPTION_LANGUAGE_HINT: str = "fr"


settings = Settings()
