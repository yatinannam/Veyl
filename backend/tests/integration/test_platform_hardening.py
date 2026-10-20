"""Platform settings from the Phase 0 and Phase 4 reviews."""

import logging
import time

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app import health
from app.config import Settings
from app.main import create_app
from tests.api import API, client_for
from tests.conftest import DatabaseUrls

PLACEHOLDER_KEY = "change-me-to-a-long-random-string-0123456789"


def test_the_example_audit_key_is_refused_in_production() -> None:
    with pytest.raises(ValidationError, match="AUDIT_HMAC_KEY"):
        Settings(
            database_url="postgresql+psycopg://u:p@h/d",
            environment="production",
            audit_hmac_key=PLACEHOLDER_KEY,
        )
    # Development keeps working with the example file.
    Settings(database_url="postgresql+psycopg://u:p@h/d", audit_hmac_key=PLACEHOLDER_KEY)


def test_log_level_applies_to_the_api() -> None:
    create_app(Settings(database_url="postgresql+psycopg://u:p@h/d", log_level="warning"))
    assert logging.getLogger("app").level == logging.WARNING


def test_readiness_gives_up_on_a_database_that_stalls(
    database_urls: DatabaseUrls, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A database that accepts the connection but never answers.
    monkeypatch.setattr(health, "READINESS_QUERY", "SELECT pg_sleep(30)")
    started = time.monotonic()
    with client_for(database_urls.app) as client:
        response = client.get(f"{API}/readyz")
    assert response.status_code == 503
    assert time.monotonic() - started < 10


@pytest.mark.parametrize("database", ["postgres", "template1"])
def test_the_app_role_cannot_open_the_maintenance_databases(
    database_urls: DatabaseUrls, database: str
) -> None:
    url = database_urls.app.rsplit("/", 1)[0] + f"/{database}"
    engine = create_engine(url)
    try:
        with pytest.raises(OperationalError, match="permission denied"), engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    finally:
        engine.dispose()
