from typing import Any

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.factories import DEFAULT_PASSWORD

API = "/api/v1"


def client_for(database_url: str, **settings: Any) -> TestClient:
    """Use as a context manager so the app lifespan (engine) runs."""
    return TestClient(create_app(Settings(database_url=database_url, **settings)))


def login(client: TestClient, slug: str, email: str, password: str = DEFAULT_PASSWORD) -> Any:
    return client.post(
        f"{API}/auth/login", json={"tenant_slug": slug, "email": email, "password": password}
    )


def csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("veyl_csrf") or ""}


def set_cookies(response: Any) -> list[str]:
    return [v for k, v in response.headers.multi_items() if k.lower() == "set-cookie"]
