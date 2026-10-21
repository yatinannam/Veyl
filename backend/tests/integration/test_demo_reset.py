"""The nightly reset of the public demo. Uses its own copies of the two companies (other
slugs), so the shared demo world other tests read is never touched."""

import dataclasses
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select

from app import cli
from app.db.schema import audit_events, conversations, documents, sessions, tenants, users
from app.demo.content import ACME, GLOBEX, DemoTenant
from app.demo.reset import DemoMissing, reset_demo
from app.demo.seed import seed_demo
from app.documents.storage import LocalStorage
from app.ingestion.pipeline import Ingestor
from tests.api import API, client_for, csrf, login, upload
from tests.conftest import DatabaseUrls
from tests.demo_support import DEMO_PASSWORD, TestClientTransport
from tests.factories import drain_jobs
from tests.fakes import FakeEmbedder

pytestmark = pytest.mark.integration


@dataclasses.dataclass
class Demo:
    tenants: tuple[DemoTenant, ...]
    database_url: str
    upload_dir: Path
    owner_engine: Engine
    app_engine: Engine

    def client(self) -> TestClient:
        return client_for(self.database_url, upload_dir=str(self.upload_dir))

    def reset(self) -> Any:
        with ExitStack() as stack:

            def new_transport() -> TestClientTransport:
                return TestClientTransport(stack.enter_context(self.client()))

            report = reset_demo(self.owner_engine, new_transport, DEMO_PASSWORD, self.tenants)
        self.ingest()
        return report

    def ingest(self) -> None:
        ingestor = Ingestor(
            self.app_engine, LocalStorage(self.upload_dir), FakeEmbedder(), max_pdf_pages=10
        )
        while ingestor.run_once():
            pass


@pytest.fixture
def demo(
    request: pytest.FixtureRequest,
    owner_engine: Engine,
    app_engine: Engine,
    database_urls: DatabaseUrls,
    tmp_path: Path,
) -> Iterator[Demo]:
    suffix = request.node.name[-12:].replace("_", "-").strip("-")
    copies = (
        dataclasses.replace(ACME, slug=f"acme-{suffix}"),
        dataclasses.replace(GLOBEX, slug=f"globex-{suffix}"),
    )
    drain_jobs(owner_engine)
    world = Demo(copies, database_urls.app, tmp_path, owner_engine, app_engine)
    with ExitStack() as stack:

        def new_transport() -> TestClientTransport:
            return TestClientTransport(stack.enter_context(world.client()))

        seed_demo(owner_engine, new_transport, DEMO_PASSWORD, copies)
    world.ingest()
    yield world


def _state(engine: Engine, slug: str) -> dict[str, Any]:
    with engine.connect() as conn:
        tenant = conn.execute(select(tenants).where(tenants.c.slug == slug)).one()
        people = {
            r.email: (r.role, r.is_active)
            for r in conn.execute(select(users).where(users.c.tenant_id == tenant.id))
        }
        docs = list(
            conn.execute(
                select(documents).where(
                    documents.c.tenant_id == tenant.id, documents.c.deleted_at.is_(None)
                )
            )
        )
        live_sessions = conn.execute(
            select(func.count())
            .select_from(sessions)
            .where(sessions.c.tenant_id == tenant.id, sessions.c.revoked_at.is_(None))
        ).scalar_one()
        counts = {
            name: conn.execute(
                select(func.count()).select_from(table).where(table.c.tenant_id == tenant.id)
            ).scalar_one()
            for name, table in (
                ("conversations", conversations),
                ("audit", audit_events),
                ("all_documents", documents),
            )
        }
        resets = conn.execute(
            select(func.count())
            .select_from(audit_events)
            .where(audit_events.c.tenant_id == tenant.id, audit_events.c.event_type == "demo.reset")
        ).scalar_one()
    return {
        "tenant": tenant,
        "people": people,
        "docs": docs,
        "resets": resets,
        "sessions": live_sessions,
        **counts,
    }


def _seeded_people(tenant: DemoTenant) -> dict[str, tuple[str, bool]]:
    return {p.email: (p.role, True) for p in (tenant.admin, *tenant.people)}


def _ids(client: TestClient) -> dict[str, str]:
    return {u["email"]: u["id"] for u in client.get(f"{API}/users").json()}


def _documents(client: TestClient) -> dict[str, str]:
    return {d["title"]: d["id"] for d in client.get(f"{API}/documents").json()}


def test_reset_undoes_what_visitors_did(demo: Demo) -> None:
    acme = demo.tenants[0]
    with demo.client() as alice:
        assert login(alice, acme.slug, acme.admin.email, DEMO_PASSWORD).status_code == 200
        # A visitor adds an admin, uploads a file, demotes and deactivates people, re-shares
        # one seed document, deletes another, renames the workspace, and asks a question.
        visitor = alice.post(
            f"{API}/users",
            json={"email": "visitor@acme.test", "password": "visitor-password", "role": "admin"},
            headers=csrf(alice),
        ).json()
        ids = _ids(alice)
        docs = _documents(alice)
        alice.patch(
            f"{API}/users/{ids['mark@acme.test']}", json={"role": "member"}, headers=csrf(alice)
        )
        alice.patch(
            f"{API}/users/{ids['mia@acme.test']}", json={"is_active": False}, headers=csrf(alice)
        )
        alice.put(
            f"{API}/documents/{docs['Employee Handbook']}/permissions",
            json={"classification": "confidential", "allowed_roles": ["admin"]},
            headers=csrf(alice),
        )
        alice.delete(f"{API}/documents/{docs['IT Help FAQ']}", headers=csrf(alice))
        alice.patch(
            f"{API}/tenant",
            json={"name": "Renamed", "audit_query_mode": "plaintext"},
            headers=csrf(alice),
        )
        assert upload(alice, "visitor.md", b"# Visitor\n\nNotes.").status_code == 202
        alice.post(
            f"{API}/query", json={"question": "What is the leave policy?"}, headers=csrf(alice)
        )
        demo.ingest()
    with demo.client() as intruder:
        login(intruder, acme.slug, "visitor@acme.test", "visitor-password")
        intruder.patch(
            f"{API}/users/{ids[acme.admin.email]}",
            json={"role": "member"},
            headers=csrf(intruder),
        )
        before = _state(demo.owner_engine, acme.slug)
        assert before["people"][acme.admin.email] == ("member", True)
        assert before["conversations"] > 0

        report = demo.reset()

        # The visitor's session no longer works, and the visitor no longer exists.
        assert intruder.get(f"{API}/auth/me").status_code == 401
    after = _state(demo.owner_engine, acme.slug)
    assert after["people"] == _seeded_people(acme)
    assert visitor["email"] not in after["people"]
    assert (after["tenant"].name, after["tenant"].audit_query_mode) == ("Acme Corp", "hash")
    assert (after["sessions"], after["conversations"]) == (0, 0)
    # One live, ready copy of each seed document and nothing else, not even deleted rows.
    assert sorted(d.title for d in after["docs"]) == sorted(d.title for d in acme.documents)
    assert after["all_documents"] == len(acme.documents)
    assert all(d.status == "ready" for d in after["docs"])
    handbook = next(d for d in after["docs"] if d.title == "Employee Handbook")
    assert (handbook.classification, sorted(handbook.allowed_roles)) == (
        "internal",
        ["admin", "manager", "member"],
    )
    assert sorted(report.documents_reuploaded) == ["Employee Handbook", "IT Help FAQ"]
    # Audit records are only ever added.
    assert after["audit"] > before["audit"]
    assert after["resets"] == 1

    with demo.client() as again:
        assert login(again, acme.slug, acme.admin.email, DEMO_PASSWORD).status_code == 200
        assert again.get(f"{API}/auth/me").json()["user"]["role"] == "admin"
        mark_review = _documents(again)["Mark's Performance Review"]
        shared = again.get(f"{API}/documents/{mark_review}").json()["allowed_user_ids"]
        assert _ids(again)["mark@acme.test"] in shared


def test_reset_of_an_untouched_demo_keeps_every_document(demo: Demo) -> None:
    before = {t.slug: _state(demo.owner_engine, t.slug) for t in demo.tenants}
    report = demo.reset()
    assert report.documents_reuploaded == []
    assert report.documents_removed == 0
    for tenant in demo.tenants:
        after = _state(demo.owner_engine, tenant.slug)
        assert {d.id for d in after["docs"]} == {d.id for d in before[tenant.slug]["docs"]}
        assert after["people"] == _seeded_people(tenant)


def test_reset_refuses_when_the_demo_was_never_seeded(owner_engine: Engine) -> None:
    missing = (dataclasses.replace(ACME, slug="never-seeded"),)
    with pytest.raises(DemoMissing):
        reset_demo(owner_engine, lambda: None, DEMO_PASSWORD, missing)  # type: ignore[arg-type,return-value]


def test_cli_reset_needs_the_password_and_the_owner(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("MIGRATIONS_DATABASE_URL", raising=False)
    monkeypatch.delenv("ADMIN_DATABASE_URL", raising=False)
    monkeypatch.setenv("VEYL_DEMO_PASSWORD", DEMO_PASSWORD)
    assert cli.main(["reset-demo", "--api-url", "http://unused"]) == 2
    assert "MIGRATIONS_DATABASE_URL" in capsys.readouterr().err


def test_cli_derives_the_owner_connection_from_the_admin_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MIGRATIONS_DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "ADMIN_DATABASE_URL",
        "postgresql://neondb_owner:admin-pw@ep-x.neon.tech/neondb?sslmode=require",
    )
    monkeypatch.setenv("VEYL_OWNER_PASSWORD", "owner-pw")
    assert cli._owner_url() == (
        "postgresql+psycopg://veyl_owner:owner-pw@ep-x.neon.tech/neondb?sslmode=require"
    )
