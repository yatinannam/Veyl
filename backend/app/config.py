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

    @property
    def cookie_secure(self) -> bool:
        return self.environment == "production"
