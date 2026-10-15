from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_healthz_is_ok_without_database() -> None:
    app = create_app(Settings(database_url="postgresql+psycopg://x:x@127.0.0.1:1/x"))
    with TestClient(app) as client:
        response = client.get("/api/v1/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_served_under_api_prefix() -> None:
    app = create_app(Settings(database_url="postgresql+psycopg://x:x@127.0.0.1:1/x"))
    with TestClient(app) as client:
        assert client.get("/api/v1/openapi.json").status_code == 200
