from collections.abc import Iterable
from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient

from app.config import Settings
from app.llm.client import LLMClient
from app.main import create_app
from app.retrieval.embeddings import Embedder
from tests.factories import DEFAULT_PASSWORD
from tests.fakes import FakeEmbedder, RecordingLLM

API = "/api/v1"


def client_for(
    database_url: str,
    *,
    embedder: Embedder | None = None,
    llm: LLMClient | None = None,
    **settings: Any,
) -> TestClient:
    """Use as a context manager so the app lifespan (engine) runs. Fakes by default: tests
    never load the embedding model or call a real LLM."""
    settings.setdefault("retrieval_min_score", 0.0)
    return TestClient(
        create_app(
            Settings(database_url=database_url, **settings),
            embedder=embedder or FakeEmbedder(),
            llm=llm or RecordingLLM(),
        )
    )


def login(client: TestClient, slug: str, email: str, password: str = DEFAULT_PASSWORD) -> Any:
    return client.post(
        f"{API}/auth/login", json={"tenant_slug": slug, "email": email, "password": password}
    )


def csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("veyl_csrf") or ""}


def set_cookies(response: Any) -> list[str]:
    return [v for k, v in response.headers.multi_items() if k.lower() == "set-cookie"]


def upload(
    client: TestClient,
    filename: str,
    data: bytes,
    *,
    title: str = "Doc",
    classification: str = "internal",
    roles: Iterable[str] = (),
    user_ids: Iterable[UUID] = (),
    extra: dict[str, str] | None = None,
) -> Any:
    form: dict[str, Any] = {
        "title": title,
        "classification": classification,
        "allowed_roles": list(roles),
        "allowed_user_ids": [str(u) for u in user_ids],
        **(extra or {}),
    }
    return client.post(
        f"{API}/documents",
        files={"file": (filename, data, "application/octet-stream")},
        data=form,
        headers=csrf(client),
    )
