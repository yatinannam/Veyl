from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Read from environment variables only (no .env loading):
    Docker Compose injects them, so a missing value fails loudly instead of defaulting."""

    model_config = SettingsConfigDict(extra="ignore")

    database_url: str
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "info"
