import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from testcontainers.community.postgres import PostgresContainer

if TYPE_CHECKING:
    from tests.demo_support import DemoWorld

BACKEND_DIR = Path(__file__).resolve().parents[1]
INIT_DIR = BACKEND_DIR.parent / "docker" / "postgres" / "init"

# Required setting; tests never load .env.
os.environ.setdefault("AUDIT_HMAC_KEY", "test-audit-hmac-key-0123456789abcdef")

OWNER_PASSWORD = "owner-test-pw"
APP_PASSWORD = "app-test-pw"


@dataclass(frozen=True)
class DatabaseUrls:
    owner: str
    app: str


def _wait_for_login(url: str, timeout_s: float = 60.0) -> None:
    """The image runs init scripts on a socket-only temporary server, then restarts.
    Logging in over TCP as veyl_app proves the real server is up AND roles exist."""
    deadline = time.monotonic() + timeout_s
    engine = create_engine(url)
    try:
        while True:
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                return
            except OperationalError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
    finally:
        engine.dispose()


def _migrate(owner_url: str) -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", owner_url)
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def database_urls() -> Iterator[DatabaseUrls]:
    container = (
        PostgresContainer(
            "pgvector/pgvector:pg16",
            username="postgres",
            password="postgres-test-pw",
            dbname="veyl",
        )
        .with_env("VEYL_OWNER_PASSWORD", OWNER_PASSWORD)
        .with_env("VEYL_APP_PASSWORD", APP_PASSWORD)
        .with_volume_mapping(str(INIT_DIR), "/docker-entrypoint-initdb.d", "ro")
    )
    with container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(5432)
        base = f"{host}:{port}/veyl"
        urls = DatabaseUrls(
            owner=f"postgresql+psycopg://veyl_owner:{OWNER_PASSWORD}@{base}",
            app=f"postgresql+psycopg://veyl_app:{APP_PASSWORD}@{base}",
        )
        _wait_for_login(urls.app)
        _migrate(urls.owner)
        yield urls


@pytest.fixture(scope="session")
def owner_engine(database_urls: DatabaseUrls) -> Iterator[Engine]:
    engine = create_engine(database_urls.owner)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app_engine(database_urls: DatabaseUrls) -> Iterator[Engine]:
    engine = create_engine(database_urls.app)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def demo_world(
    owner_engine: Engine,
    app_engine: Engine,
    database_urls: DatabaseUrls,
    tmp_path_factory: pytest.TempPathFactory,
) -> "DemoWorld":
    """The spec section 14 demo, seeded once per session through the real API."""
    from tests.demo_support import seed_through_api

    return seed_through_api(
        owner_engine, app_engine, database_urls.app, tmp_path_factory.mktemp("demo-uploads")
    )
