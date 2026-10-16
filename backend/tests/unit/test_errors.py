import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.errors import Forbidden, RateLimited
from app.main import create_app

DB = "postgresql+psycopg://x:x@127.0.0.1:1/x"


class _Body(BaseModel):
    password: str
    age: int


def _app() -> FastAPI:
    app = create_app(Settings(database_url=DB))

    @app.get("/test/forbidden")
    def forbidden() -> None:
        raise Forbidden()

    @app.get("/test/limited")
    def limited() -> None:
        raise RateLimited()

    @app.post("/test/validate")
    def validate(body: _Body) -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/test/db-down")
    def db_down() -> None:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    @app.get("/test/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    return app


def test_app_errors_use_the_envelope_with_trace_id() -> None:
    with TestClient(_app()) as client:
        r = client.get("/test/forbidden")
    assert r.status_code == 403
    error = r.json()["error"]
    assert error["code"] == "forbidden"
    assert error["trace_id"] == r.headers["X-Trace-Id"]


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/test/limited", 429, "rate_limited"),
        ("/test/db-down", 503, "service_unavailable"),
        ("/does-not-exist", 404, "not_found"),
    ],
)
def test_status_and_code_mapping(path: str, status: int, code: str) -> None:
    with TestClient(_app()) as client:
        r = client.get(path)
    assert r.status_code == status
    assert r.json()["error"]["code"] == code


def test_validation_errors_never_echo_submitted_values() -> None:
    with TestClient(_app()) as client:
        r = client.post("/test/validate", json={"password": "hunter2-very-secret", "age": "nope-x"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    assert "hunter2-very-secret" not in r.text
    assert "nope-x" not in r.text


def test_unhandled_errors_return_generic_500() -> None:
    with TestClient(_app(), raise_server_exceptions=False) as client:
        r = client.get("/test/boom")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"
    assert "secret internal detail" not in r.text
