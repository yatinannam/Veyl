"""A minimal client for the Veyl API, used by the demo seed. Standard library only: the
seed is a client of the public API like any other, so it needs cookies, the CSRF header,
JSON, and multipart uploads, and nothing else."""

import json as jsonlib
import secrets
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.cookiejar import CookieJar
from typing import Any, Protocol

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    body: Any


@dataclass(frozen=True, slots=True)
class Multipart:
    fields: list[tuple[str, str]] = field(default_factory=list)
    # (field name, filename, content type, content)
    files: list[tuple[str, str, str, bytes]] = field(default_factory=list)


class Transport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        multipart: Multipart | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response: ...

    def cookie(self, name: str) -> str | None: ...


class DemoApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code


def encode_multipart(form: Multipart) -> tuple[bytes, str]:
    boundary = f"veyl-{secrets.token_hex(16)}"
    lines: list[bytes] = []
    for name, value in form.fields:
        lines += [
            f"--{boundary}".encode(),
            f'Content-Disposition: form-data; name="{name}"'.encode(),
            b"",
            value.encode(),
        ]
    for name, filename, content_type, content in form.files:
        lines += [
            f"--{boundary}".encode(),
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'.encode(),
            f"Content-Type: {content_type}".encode(),
            b"",
            content,
        ]
    lines += [f"--{boundary}--".encode(), b""]
    return b"\r\n".join(lines), f"multipart/form-data; boundary={boundary}"


class UrllibTransport:
    """Talks to a running API, e.g. http://api:8000 inside Compose."""

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        multipart: Multipart | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        all_headers = {"Accept": "application/json", **(headers or {})}
        data: bytes | None = None
        if multipart is not None:
            data, all_headers["Content-Type"] = encode_multipart(multipart)
        elif json is not None:
            data = jsonlib.dumps(json).encode()
            all_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(  # noqa: S310 (operator-supplied API URL)
            f"{self.base_url}{path}", data=data, headers=all_headers, method=method
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, raw = error.code, error.read()
        return Response(status, jsonlib.loads(raw) if raw else None)

    def cookie(self, name: str) -> str | None:
        return next((c.value for c in self.jar if c.name == name), None)


class ApiSession:
    """One signed-in user: JSON in and out, CSRF header on state-changing requests,
    errors raised with the API's error code."""

    def __init__(self, transport: Transport, prefix: str = "/api/v1") -> None:
        self.transport = transport
        self.prefix = prefix

    def _send(
        self, method: str, path: str, *, json: Any = None, multipart: Multipart | None = None
    ) -> Any:
        headers: dict[str, str] = {}
        if method in UNSAFE_METHODS:
            headers["X-CSRF-Token"] = self.transport.cookie("veyl_csrf") or ""
        response = self.transport.request(
            method, f"{self.prefix}{path}", json=json, multipart=multipart, headers=headers
        )
        if response.status >= 400:
            error = (
                (response.body or {}).get("error", {}) if isinstance(response.body, dict) else {}
            )
            raise DemoApiError(
                response.status, error.get("code", "http_error"), error.get("message", "")
            )
        return response.body

    def get(self, path: str) -> Any:
        return self._send("GET", path)

    def post(self, path: str, *, json: Any = None, multipart: Multipart | None = None) -> Any:
        return self._send("POST", path, json=json, multipart=multipart)

    def put(self, path: str, *, json: Any = None) -> Any:
        return self._send("PUT", path, json=json)

    def login(self, slug: str, email: str, password: str) -> Any:
        return self.post(
            "/auth/login", json={"tenant_slug": slug, "email": email, "password": password}
        )
