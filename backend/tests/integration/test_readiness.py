import re
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import DatabaseUrls


@pytest.mark.integration
def test_readyz_ready_when_database_reachable(database_urls: DatabaseUrls) -> None:
    app = create_app(Settings(database_url=database_urls.app))
    with TestClient(app) as client:
        response = client.get("/api/v1/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readyz_fails_closed_when_database_unreachable() -> None:
    app = create_app(Settings(database_url="postgresql+psycopg://veyl_app:x@127.0.0.1:1/veyl"))
    started = time.monotonic()
    with TestClient(app) as client:
        response = client.get("/api/v1/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert time.monotonic() - started < 10


def test_readiness_failures_log_the_reason_without_the_password(
    caplog: pytest.LogCaptureFixture,
) -> None:
    url = "postgresql://veyl_app:Sup3r-Secret-Pw@127.0.0.1:1/veyl?sslmode=disable"
    app = create_app(Settings(database_url=url))
    with caplog.at_level("WARNING"), TestClient(app) as client:
        client.get("/api/v1/readyz")
    logged = " ".join(r.getMessage() for r in caplog.records)
    # The driver's error class and message, e.g. "(ConnectionTimeout: ...)".
    assert re.search(r"readiness check failed: database unreachable \(\w+: .+\)", logged)
    assert "Sup3r-Secret-Pw" not in logged
