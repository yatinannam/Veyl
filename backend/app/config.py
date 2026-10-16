from typing import Literal

from pydantic import Field, SecretStr
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
    retrieval_min_score: float = Field(default=0.0, ge=-1.0, le=1.0)
    retrieval_cache_ttl_seconds: int = Field(default=600, ge=0)
    worker_poll_seconds: float = Field(default=2.0, gt=0)

    @property
    def cookie_secure(self) -> bool:
        return self.environment == "production"
