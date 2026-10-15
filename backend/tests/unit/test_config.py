import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_require_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings()


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h:5432/d")
    monkeypatch.setenv("ENVIRONMENT", "test")
    s = Settings()
    assert s.database_url == "postgresql+psycopg://u:p@h:5432/d"
    assert s.environment == "test"


def test_settings_reject_unknown_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h:5432/d")
    monkeypatch.setenv("ENVIRONMENT", "staging-ish")
    with pytest.raises(ValidationError):
        Settings()
