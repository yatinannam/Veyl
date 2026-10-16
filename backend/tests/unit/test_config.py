import pytest
from pydantic import ValidationError

from app.config import Settings

URL = "postgresql+psycopg://u:secretpw@h:5432/d"


def test_settings_require_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings()


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("ENVIRONMENT", "test")
    s = Settings()
    assert s.database_url.get_secret_value() == URL
    assert s.environment == "test"


def test_settings_reject_unknown_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("ENVIRONMENT", "staging-ish")
    with pytest.raises(ValidationError):
        Settings()


def test_settings_never_reveal_database_password() -> None:
    s = Settings(database_url=URL)
    assert "secretpw" not in repr(s)
    assert "secretpw" not in str(s.model_dump())


@pytest.mark.parametrize(
    ("environment", "secure"),
    [("development", False), ("test", False), ("production", True)],
)
def test_cookies_are_secure_only_in_production(environment: str, secure: bool) -> None:
    assert Settings(database_url=URL, environment=environment).cookie_secure is secure


def test_session_ttl_has_sane_bounds() -> None:
    with pytest.raises(ValidationError):
        Settings(database_url=URL, session_ttl_minutes=1)


def test_audit_hmac_key_is_required_and_long(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUDIT_HMAC_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(database_url=URL)
    with pytest.raises(ValidationError):
        Settings(database_url=URL, audit_hmac_key="too-short")
    assert "x" * 40 not in repr(Settings(database_url=URL, audit_hmac_key="x" * 40))


def test_llm_defaults_point_at_a_free_provider() -> None:
    settings = Settings(database_url=URL)
    assert settings.llm_base_url == "https://api.groq.com/openai/v1"
    assert settings.llm_api_key is None
