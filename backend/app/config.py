from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Read from environment variables only (no .env loading):
    Docker Compose injects them, so a missing value fails loudly instead of defaulting."""

    model_config = SettingsConfigDict(extra="ignore")

    database_url: SecretStr
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "info"
    session_ttl_minutes: int = Field(default=720, ge=5, le=10080)
    login_max_failures: int = Field(default=5, ge=1)
    login_window_seconds: int = Field(default=900, ge=1)
    login_max_concurrent_hashes: int = Field(default=4, ge=1)

    upload_dir: str = "/data/uploads"
    max_upload_mb: int = Field(default=10, ge=1, le=100)
    max_pdf_pages: int = Field(default=200, ge=1)
    max_extracted_chars: int = Field(default=2_000_000, ge=1000)
    extraction_timeout_seconds: float = Field(default=60.0, gt=0)
    extraction_memory_mb: int = Field(default=1024, ge=64)
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: str | None = None
    retrieval_top_k: int = Field(default=6, ge=1, le=50)
    # Measured on bge-small: relevant top scores 0.60-0.78, unrelated questions <= 0.45.
    retrieval_min_score: float = Field(default=0.5, ge=-1.0, le=1.0)
    retrieval_cache_ttl_seconds: int = Field(default=600, ge=0)
    worker_poll_seconds: float = Field(default=2.0, gt=0)

    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "openai/gpt-oss-120b"
    llm_api_key: SecretStr | None = None
    llm_timeout_seconds: float = Field(default=30.0, gt=0)
    llm_max_tokens: int = Field(default=1024, ge=16, le=8192)
    history_max_turns: int = Field(default=6, ge=0, le=20)
    audit_hmac_key: SecretStr

    @field_validator("audit_hmac_key")
    @classmethod
    def _audit_key_is_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("AUDIT_HMAC_KEY must be at least 32 characters")
        return value

    @field_validator("log_level")
    @classmethod
    def _known_log_level(cls, value: str) -> str:
        if value.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be one of debug, info, warning, error, critical")
        return value.lower()

    @model_validator(mode="after")
    def _no_example_secrets_in_production(self) -> "Settings":
        # The value shipped in .env.example is public; an audit reference keyed with it
        # could be reversed by anyone who guesses the question.
        if self.environment == "production" and self.audit_hmac_key.get_secret_value().startswith(
            "change-me"
        ):
            raise ValueError("AUDIT_HMAC_KEY is the example placeholder; set a random key")
        return self

    @property
    def cookie_secure(self) -> bool:
        return self.environment == "production"
