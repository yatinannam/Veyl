from email.parser import BytesParser
from email.policy import HTTP
from typing import Any

import pytest

from app.demo.http import ApiSession, DemoApiError, Multipart, Response, encode_multipart


def _parts(body: bytes, content_type: str) -> list[Any]:
    message = BytesParser(policy=HTTP).parsebytes(
        b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body
    )
    return list(message.iter_parts())


def test_multipart_round_trips_fields_and_files() -> None:
    body, content_type = encode_multipart(
        Multipart(
            fields=[("title", "Handbook"), ("allowed_roles", "member"), ("allowed_roles", "admin")],
            files=[("file", "handbook.md", "text/markdown", b"# Hello\nWorld")],
        )
    )
    assert content_type.startswith("multipart/form-data; boundary=")
    parts = _parts(body, content_type)
    named = [(p.get_param("name", header="content-disposition"), p) for p in parts]
    assert [n for n, _ in named] == ["title", "allowed_roles", "allowed_roles", "file"]
    assert named[0][1].get_content().strip() == "Handbook"
    file_part = named[3][1]
    assert file_part.get_filename() == "handbook.md"
    assert file_part.get_content_type() == "text/markdown"
    assert file_part.get_payload(decode=True) == b"# Hello\nWorld"


class FakeTransport:
    def __init__(self, response: Response) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        multipart: Multipart | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        self.calls.append((method, path, dict(headers or {})))
        return self.response

    def cookie(self, name: str) -> str | None:
        return "csrf-1" if name == "veyl_csrf" else None


def test_csrf_header_only_on_state_changing_requests() -> None:
    transport = FakeTransport(Response(200, {}))
    session = ApiSession(transport)
    session.get("/documents")
    session.post("/users", json={"email": "a@b.test"})
    assert "X-CSRF-Token" not in transport.calls[0][2]
    assert transport.calls[1][2]["X-CSRF-Token"] == "csrf-1"


def test_error_responses_raise_with_the_api_error_code() -> None:
    transport = FakeTransport(
        Response(409, {"error": {"code": "conflict", "message": "Exists.", "trace_id": "t"}})
    )
    with pytest.raises(DemoApiError) as raised:
        ApiSession(transport).post("/users", json={})
    assert raised.value.status == 409
    assert raised.value.code == "conflict"
