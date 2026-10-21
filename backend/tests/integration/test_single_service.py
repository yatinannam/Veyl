"""Running as one small hosted service: ingestion inside the API process, the visitor IP
from the dashboard's proxy, and a cap on documents per workspace."""

import time
from pathlib import Path

import pytest
from sqlalchemy import Engine

from tests.api import API, client_for, csrf, login, upload
from tests.conftest import DatabaseUrls
from tests.factories import create_tenant_with_admin, drain_jobs

pytestmark = pytest.mark.integration

SECRET = "proxy-secret-0123456789abcdef-0123456789"


def test_the_api_ingests_uploads_in_a_background_thread(
    owner_engine: Engine, database_urls: DatabaseUrls, tmp_path: Path
) -> None:
    drain_jobs(owner_engine)
    t = create_tenant_with_admin(owner_engine)
    with client_for(
        database_urls.app,
        run_ingestion_worker=True,
        worker_poll_seconds=0.1,
        upload_dir=str(tmp_path),
    ) as client:
        worker = client.app.state.ingestion_worker  # type: ignore[attr-defined]
        assert worker.alive
        login(client, t.slug, t.admin_email)
        document_id = upload(client, "notes.md", b"# Notes\n\nThe launch is in May.").json()["id"]
        deadline = time.monotonic() + 30
        status = ""
        while time.monotonic() < deadline:
            status = client.get(f"{API}/documents/{document_id}").json()["status"]
            if status == "ready":
                break
            time.sleep(0.1)
        assert status == "ready"
    # Leaving the client runs the lifespan shutdown, which stops the thread.
    assert not worker.alive


def test_without_the_setting_no_thread_is_started(database_urls: DatabaseUrls) -> None:
    with client_for(database_urls.app) as client:
        assert client.app.state.ingestion_worker is None  # type: ignore[attr-defined]


def _fail_logins(client: object, ip: str, secret: str, attempts: int) -> list[int]:
    codes = []
    for i in range(attempts):
        response = client.post(  # type: ignore[attr-defined]
            f"{API}/auth/login",
            json={"tenant_slug": "no-such", "email": f"p{i}-{ip}@x.test", "password": "wrong-pass"},
            headers={"X-Veyl-Proxy-Secret": secret, "X-Veyl-Client-IP": ip},
        )
        codes.append(response.status_code)
    return codes


def test_the_proxy_named_visitor_is_the_rate_limit_key(database_urls: DatabaseUrls) -> None:
    # IP limit is 4 x max_failures = 4.
    with client_for(database_urls.app, proxy_shared_secret=SECRET, login_max_failures=1) as client:
        assert _fail_logins(client, "203.0.113.10", SECRET, 5)[-1] == 429
        # A different visitor behind the same proxy is not affected.
        assert _fail_logins(client, "203.0.113.11", SECRET, 1) == [401]


def test_a_forged_visitor_ip_without_the_secret_does_not_escape_the_limit(
    database_urls: DatabaseUrls,
) -> None:
    with client_for(database_urls.app, proxy_shared_secret=SECRET, login_max_failures=1) as client:
        codes = [
            _fail_logins(client, f"203.0.113.{20 + i}", "forged-secret", 1)[0] for i in range(5)
        ]
    assert codes[-1] == 429


def test_uploads_beyond_the_workspace_cap_are_refused(
    owner_engine: Engine, database_urls: DatabaseUrls, tmp_path: Path
) -> None:
    t = create_tenant_with_admin(owner_engine)
    with client_for(
        database_urls.app, max_documents_per_workspace=2, upload_dir=str(tmp_path)
    ) as client:
        login(client, t.slug, t.admin_email)
        first = upload(client, "a.txt", b"alpha").json()["id"]
        assert upload(client, "b.txt", b"beta").status_code == 202
        refused = upload(client, "c.txt", b"gamma")
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "document_limit_reached"
        # Deleting frees a place.
        client.delete(f"{API}/documents/{first}", headers=csrf(client))
        assert upload(client, "c.txt", b"gamma").status_code == 202
