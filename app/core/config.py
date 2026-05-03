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


settings = Settings()
