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
    # Client-side chunk duration in seconds. The worker uses ffmpeg to split
    # the uploaded audio into pieces of this length before transcribing each
    # one separately, then stitches the segments back together with offset
    # timestamps. Caps the blast radius of Whisper's repetition-loop failure
    # mode (a hallucination can only contaminate one chunk, not the rest of
    # the session). Set to 0 to disable and call the adapter once with the
    # whole file (tests use 0 to keep their fake audio bytes working).
    TRANSCRIPTION_CHUNK_DURATION_SECONDS: int = 30

    # Sous-jalon 5.5 — mode `non_diarised`. Taille maximale d'un chunk de
    # transcription stocké dans jdr_chunks (en caractères). ~30 000 chars
    # ≈ 7 500-10 000 tokens FR, confortable pour un contexte 32k tokens avec
    # marge prompt + sortie. À affiner par benchmarks empiriques.
    KAEYRIS_CHUNK_MAX_CHARS: int = 30000

    # Web auth sessions. Cookies are HTTP-only and issued by the API after
    # username/password login. CORS origins stay explicit when credentials
    # are enabled.
    CORS_ALLOWED_ORIGINS: str = ""
    SESSION_COOKIE_NAME: str = "session"
    SESSION_COOKIE_SECURE: bool = False
    SESSION_COOKIE_SAMESITE: str = "lax"
    WEB_SESSION_TTL_SECONDS: int = 28800

    @property
    def cors_allowed_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.CORS_ALLOWED_ORIGINS.split(",")
            if origin.strip()
        ]


settings = Settings()
if settings.KAEYRIS_CHUNK_MAX_CHARS <= 0:
    raise RuntimeError(
        "KAEYRIS_CHUNK_MAX_CHARS must be strictly positive "
        f"(got {settings.KAEYRIS_CHUNK_MAX_CHARS!r})."
    )
if settings.WEB_SESSION_TTL_SECONDS <= 0:
    raise RuntimeError(
        "WEB_SESSION_TTL_SECONDS must be strictly positive "
        f"(got {settings.WEB_SESSION_TTL_SECONDS!r})."
    )
