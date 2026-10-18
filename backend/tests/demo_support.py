"""Seeds the demo through the real API (FastAPI TestClient) so tests exercise exactly the
code path `python -m app.cli seed-demo` uses against a running stack."""

from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.demo.http import Multipart, Response
from app.demo.seed import DemoSummary, seed_demo
from app.documents.storage import LocalStorage
from app.ingestion.pipeline import Ingestor
from tests.api import client_for
from tests.factories import drain_jobs
from tests.fakes import FakeEmbedder

DEMO_PASSWORD = "demo-password-for-tests"


class TestClientTransport:
    __test__ = False

    def __init__(self, client: TestClient) -> None:
        self.client = client

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        multipart: Multipart | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        kwargs: dict[str, Any] = {"headers": headers or {}}
        if json is not None:
            kwargs["json"] = json
        if multipart is not None:
            data: dict[str, list[str]] = {}
            for name, value in multipart.fields:
                data.setdefault(name, []).append(value)
            kwargs["data"] = data
            kwargs["files"] = {
                name: (filename, content, content_type)
                for name, filename, content_type, content in multipart.files
            }
        response = self.client.request(method, path, **kwargs)
        body = response.json() if response.content else None
        return Response(response.status_code, body)

    def cookie(self, name: str) -> str | None:
        return self.client.cookies.get(name)


@dataclass
class DemoWorld:
    summary: DemoSummary
    upload_dir: Path
    database_url: str

    def client(self, **kwargs: Any) -> TestClient:
        return client_for(self.database_url, upload_dir=str(self.upload_dir), **kwargs)


def seed_through_api(
    owner_engine: Engine, app_engine: Engine, database_url: str, upload_dir: Path
) -> DemoWorld:
    drain_jobs(owner_engine)
    with ExitStack() as stack:

        def new_transport() -> TestClientTransport:
            client = stack.enter_context(client_for(database_url, upload_dir=str(upload_dir)))
            return TestClientTransport(client)

        summary = seed_demo(owner_engine, new_transport, DEMO_PASSWORD)
    ingestor = Ingestor(app_engine, LocalStorage(upload_dir), FakeEmbedder(), max_pdf_pages=10)
    while ingestor.run_once():
        pass
    return DemoWorld(summary, upload_dir, database_url)
