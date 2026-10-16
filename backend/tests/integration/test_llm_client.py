"""Talks to a real local HTTP server that imitates an OpenAI-compatible provider."""

import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

import pytest

from app.llm.client import ChatMessage, LLMError, OpenAICompatibleLLM


class FakeProvider:
    def __init__(self) -> None:
        self.mode = "ok"
        self.redirect_to = ""
        self.requests: list[dict[str, Any]] = []


class _Recorder(BaseHTTPRequestHandler):
    """A second server: anything arriving here followed a redirect."""

    seen: ClassVar[list[dict[str, str]]] = []

    def _record(self) -> None:
        _Recorder.seen.append(dict(self.headers))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"choices": [{"message": {"content": "hijacked"}}]}')

    do_GET = _record
    do_POST = _record

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def provider() -> Iterator[tuple[FakeProvider, str]]:
    state = FakeProvider()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
            if state.mode == "slow":
                time.sleep(2)
            if state.mode == "redirect":
                self.send_response(302)
                self.send_header("Location", state.redirect_to)
                self.end_headers()
                return
            if state.mode == "unauthorized":
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b'{"error": {"message": "invalid api key"}}')
                return
            payload: bytes
            if state.mode == "garbage":
                payload = b"<html>not json</html>"
            elif state.mode == "shape":
                payload = json.dumps({"choices": []}).encode()
            else:
                payload = json.dumps(
                    {
                        "id": "resp-1",
                        "model": body["model"],
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": " Economy only [S1]. ",
                                    "reasoning": "secret chain of thought",
                                }
                            }
                        ],
                    }
                ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state, f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()


def _client(
    base_url: str, api_key: str | None = "sk-test", timeout: float = 5
) -> OpenAICompatibleLLM:
    return OpenAICompatibleLLM(
        base_url=base_url,
        model="test-model",
        api_key=api_key,
        timeout_seconds=timeout,
        max_tokens=64,
    )


MESSAGES = [ChatMessage("system", "rules"), ChatMessage("user", "question")]


def test_completion_returns_content_only(provider: tuple[FakeProvider, str]) -> None:
    state, url = provider
    completion = _client(url).complete(MESSAGES)
    assert (completion.text, completion.model, completion.response_id) == (
        "Economy only [S1].",
        "test-model",
        "resp-1",
    )
    request = state.requests[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer sk-test"
    assert request["body"]["messages"] == [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "question"},
    ]
    assert request["body"]["model"] == "test-model"


def test_no_authorization_header_without_a_key(provider: tuple[FakeProvider, str]) -> None:
    state, url = provider
    _client(url, api_key=None).complete(MESSAGES)
    assert "Authorization" not in state.requests[0]["headers"]


@pytest.mark.parametrize("mode", ["unauthorized", "garbage", "shape"])
def test_provider_errors_become_llm_errors(provider: tuple[FakeProvider, str], mode: str) -> None:
    state, url = provider
    state.mode = mode
    with pytest.raises(LLMError):
        _client(url).complete(MESSAGES)


def test_slow_providers_time_out(provider: tuple[FakeProvider, str]) -> None:
    state, url = provider
    state.mode = "slow"
    with pytest.raises(LLMError):
        _client(url, timeout=0.5).complete(MESSAGES)


def test_unreachable_providers_fail() -> None:
    with pytest.raises(LLMError):
        _client("http://127.0.0.1:1/v1", timeout=2).complete(MESSAGES)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.test/v1", "not a url"])
def test_only_http_urls_are_accepted(url: str) -> None:
    with pytest.raises(ValueError):
        _client(url)


def test_redirects_are_refused_and_the_key_never_follows(
    provider: tuple[FakeProvider, str],
) -> None:
    state, url = provider
    other = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    _Recorder.seen = []
    try:
        state.mode = "redirect"
        state.redirect_to = f"http://127.0.0.1:{other.server_address[1]}/steal"
        with pytest.raises(LLMError):
            _client(url).complete(MESSAGES)
    finally:
        other.shutdown()
    assert _Recorder.seen == []
