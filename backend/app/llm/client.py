"""Minimal OpenAI-compatible chat client (Groq, Gemini, Ollama, OpenRouter). Standard
library HTTP only. Uses `message.content`; provider-side reasoning is never surfaced."""

import json
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class LLMError(Exception):
    """The provider failed; callers fail closed."""


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    model: str
    response_id: str | None


class LLMClient(Protocol):
    model: str

    def complete(self, messages: Sequence[ChatMessage]) -> Completion: ...


class OpenAICompatibleLLM:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None,
        timeout_seconds: float,
        max_tokens: int,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("LLM base URL must be an http(s) URL")
        self.endpoint = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self._api_key = api_key or None
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens

    def complete(self, messages: Sequence[ChatMessage]) -> Completion:
        body = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "temperature": 0.1,
                "max_tokens": self.max_tokens,
            }
        ).encode()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "veyl/0.1",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        # The endpoint scheme is validated as http(s) in __init__.
        request = urllib.request.Request(  # noqa: S310
            self.endpoint, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                payload = json.loads(response.read(MAX_RESPONSE_BYTES))
        except urllib.error.HTTPError as exc:
            raise LLMError(f"provider returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise LLMError("provider unreachable or returned invalid JSON") from exc
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("unexpected provider response") from exc
        if not isinstance(content, str):
            raise LLMError("provider returned no text")
        response_id = payload.get("id")
        return Completion(
            text=content.strip(),
            model=str(payload.get("model") or self.model),
            response_id=str(response_id) if response_id is not None else None,
        )
