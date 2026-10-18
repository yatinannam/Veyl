import pytest
from sqlalchemy import Engine, func, select

from app import cli
from app.db.schema import documents, tenants, users
from app.demo.seed import AlreadySeeded, seed_demo
from tests.api import client_for
from tests.conftest import DatabaseUrls
from tests.demo_support import DEMO_PASSWORD, DemoWorld, TestClientTransport


def _tenant(owner_engine: Engine, slug: str) -> tuple[object, dict[str, str], dict[str, object]]:
    with owner_engine.connect() as conn:
        tenant_id = conn.execute(select(tenants.c.id).where(tenants.c.slug == slug)).scalar_one()
        people = {
            r.email: r.role
            for r in conn.execute(
                select(users.c.email, users.c.role).where(users.c.tenant_id == tenant_id)
            )
        }
        docs = {
            r.title: r
            for r in conn.execute(select(documents).where(documents.c.tenant_id == tenant_id))
        }
    return tenant_id, people, docs


def test_seed_creates_both_companies_through_the_api(
    owner_engine: Engine, demo_world: DemoWorld
) -> None:
    _, acme_people, acme_docs = _tenant(owner_engine, "acme")
    assert acme_people == {
        "alice@acme.test": "admin",
        "mark@acme.test": "manager",
        "mia@acme.test": "member",
    }
    assert {t: (d.classification, sorted(d.allowed_roles)) for t, d in acme_docs.items()} == {
        "Employee Handbook": ("internal", ["admin", "manager", "member"]),
        "Engineering Roadmap": ("confidential", ["admin", "manager"]),
        "Q3 Board Financials": ("restricted", []),
        "Mark's Performance Review": ("confidential", []),
        "IT Help FAQ": ("internal", ["admin", "manager", "member"]),
    }
    _, globex_people, globex_docs = _tenant(owner_engine, "globex")
    assert globex_people == {"gina@globex.test": "admin", "gary@globex.test": "member"}
    assert {t: d.classification for t, d in globex_docs.items()} == {"Merger Plan": "restricted"}
    assert all(d.status == "ready" for d in [*acme_docs.values(), *globex_docs.values()])


def test_marks_review_is_shared_with_mark_only(owner_engine: Engine, demo_world: DemoWorld) -> None:
    with owner_engine.connect() as conn:
        mark = conn.execute(
            select(users.c.id).where(users.c.email == "mark@acme.test")
        ).scalar_one()
    _, _, docs = _tenant(owner_engine, "acme")
    review = docs["Mark's Performance Review"]
    alice_id = review.uploaded_by
    assert set(review.allowed_user_ids) == {mark, alice_id}


def test_seeding_twice_is_refused_and_changes_nothing(
    owner_engine: Engine, demo_world: DemoWorld
) -> None:
    with owner_engine.connect() as conn:
        before = conn.execute(select(func.count()).select_from(documents)).scalar_one()

    def new_transport() -> TestClientTransport:
        raise AssertionError("the API must not be called when the demo already exists")

    try:
        seed_demo(owner_engine, new_transport, DEMO_PASSWORD)
    except AlreadySeeded:
        pass
    else:
        raise AssertionError("expected AlreadySeeded")
    with owner_engine.connect() as conn:
        after = conn.execute(select(func.count()).select_from(documents)).scalar_one()
    assert after == before


def test_demo_users_can_sign_in_with_the_demo_password(demo_world: DemoWorld) -> None:
    with client_for(demo_world.database_url) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"tenant_slug": "acme", "email": "mia@acme.test", "password": DEMO_PASSWORD},
        )
    assert response.status_code == 200


def test_cli_seed_demo_needs_the_password_env(
    database_urls: DatabaseUrls, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MIGRATIONS_DATABASE_URL", database_urls.owner)
    monkeypatch.delenv("VEYL_DEMO_PASSWORD", raising=False)
    assert cli.main(["seed-demo", "--api-url", "http://127.0.0.1:9"]) == 2
    assert "VEYL_DEMO_PASSWORD" in capsys.readouterr().err


def test_cli_seed_demo_reports_an_existing_demo(
    database_urls: DatabaseUrls,
    demo_world: DemoWorld,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MIGRATIONS_DATABASE_URL", database_urls.owner)
    monkeypatch.setenv("VEYL_DEMO_PASSWORD", DEMO_PASSWORD)
    assert cli.main(["seed-demo", "--api-url", "http://127.0.0.1:9"]) == 1
    assert "Demo already seeded" in capsys.readouterr().err
