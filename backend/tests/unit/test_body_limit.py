from collections.abc import Iterator

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.documents.limits import BodySizeLimit

LIMIT = 100


def _app() -> FastAPI:
    app = FastAPI()

    @app.post("/limited/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"received": len(await request.body())}

    @app.post("/open/echo")
    async def open_echo(request: Request) -> dict[str, int]:
        return {"received": len(await request.body())}

    app.add_middleware(BodySizeLimit, max_bytes=LIMIT, path_prefix="/limited")
    return app


def _chunks(total: int) -> Iterator[bytes]:
    for _ in range(total // 10):
        yield b"x" * 10


def test_body_within_limit_passes() -> None:
    with TestClient(_app()) as client:
        assert client.post("/limited/echo", content=b"x" * LIMIT).json() == {"received": LIMIT}


def test_declared_oversized_body_is_rejected() -> None:
    with TestClient(_app()) as client:
        r = client.post("/limited/echo", content=b"x" * (LIMIT + 1))
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"


def test_streamed_body_over_limit_is_rejected_without_content_length() -> None:
    with TestClient(_app()) as client:
        r = client.post("/limited/echo", content=_chunks(LIMIT * 5))
    assert r.status_code == 413


def test_other_paths_are_not_limited() -> None:
    with TestClient(_app()) as client:
        assert client.post("/open/echo", content=b"x" * (LIMIT * 3)).status_code == 200
