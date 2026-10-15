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
