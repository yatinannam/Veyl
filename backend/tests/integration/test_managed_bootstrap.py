"""Bootstrapping a managed Postgres (Neon) where the admin role can create roles but is
not a superuser. Uses its own Postgres 17 container, laid out like a fresh Neon project."""

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError
from testcontainers.community.postgres import PostgresContainer

from app import cli
from app.bootstrap import bootstrap_database
from app.db.engine import normalise_database_url
from app.provisioning import provision_tenant
from tests.conftest import _migrate

ADMIN_PASSWORD = "Admin-Pw-7f3k2q9x4v8m1n6b"
OWNER_PASSWORD = "Owner-Pw-2c8h5t1r9w3y7k4p"
APP_PASSWORD = "App-Pw-6j1d4s8g2z5x9q3e"


@dataclass
class Managed:
    admin_url: str
    owner_url: str
    app_url: str


@pytest.fixture(scope="module")
def managed() -> Iterator[Managed]:
    with PostgresContainer(
        "pgvector/pgvector:pg17", username="postgres", password="super-pw", dbname="postgres"
    ) as container:
        base = f"{container.get_container_host_ip()}:{container.get_exposed_port(5432)}"
        superuser = create_engine(f"postgresql+psycopg://postgres:super-pw@{base}/postgres")
        with superuser.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            # Like Neon's neondb_owner: can create roles and databases, not a superuser.
            conn.execute(
                text(
                    f"CREATE ROLE neon_admin LOGIN PASSWORD '{ADMIN_PASSWORD}' "
                    "CREATEROLE CREATEDB NOSUPERUSER BYPASSRLS"
                )
            )
            conn.execute(text("CREATE DATABASE neondb OWNER neon_admin"))
        superuser.dispose()
        # Neon lets its admin role create these extensions; a plain non-superuser cannot,
        # so they are created up front here.
        db_super = create_engine(f"postgresql+psycopg://postgres:super-pw@{base}/neondb")
        with db_super.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        db_super.dispose()
        yield Managed(
            # Neon hands out plain postgresql:// URLs.
            admin_url=f"postgresql://neon_admin:{ADMIN_PASSWORD}@{base}/neondb",
            owner_url=f"postgresql+psycopg://veyl_owner:{OWNER_PASSWORD}@{base}/neondb",
            app_url=f"postgresql+psycopg://veyl_app:{APP_PASSWORD}@{base}/neondb",
        )


@pytest.fixture(scope="module")
def bootstrapped(managed: Managed) -> Managed:
    bootstrap_database(managed.admin_url, OWNER_PASSWORD, APP_PASSWORD)
    _migrate(managed.owner_url)
    return managed


def test_plain_postgresql_urls_use_the_psycopg_driver() -> None:
    url = "postgresql://u:p@ep-x.neon.tech/neondb?sslmode=require&channel_binding=require"
    assert normalise_database_url(url) == (
        "postgresql+psycopg://u:p@ep-x.neon.tech/neondb?sslmode=require&channel_binding=require"
    )
    assert normalise_database_url("postgresql+psycopg://u:p@h/d") == "postgresql+psycopg://u:p@h/d"


def test_roles_have_least_privilege(bootstrapped: Managed) -> None:
    engine = create_engine(bootstrapped.owner_url)
    with engine.connect() as conn:
        rows = {
            r.rolname: r
            for r in conn.execute(
                text(
                    "SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, "
                    "rolinherit FROM pg_roles WHERE rolname IN ('veyl_owner', 'veyl_app')"
                )
            )
        }
    engine.dispose()
    for role in rows.values():
        assert (role.rolsuper, role.rolbypassrls, role.rolcreaterole, role.rolcreatedb) == (
            False,
            False,
            False,
            False,
        )
    assert rows["veyl_app"].rolinherit is False


def test_the_app_role_is_as_restricted_as_under_compose(bootstrapped: Managed) -> None:
    owner = create_engine(bootstrapped.owner_url)
    provision_tenant(
        owner, slug="managed-co", name="Managed", admin_email="a@m.test", admin_password="x" * 14
    )
    with owner.begin() as conn:
        # Created after bootstrap: default privileges must keep it from the app role.
        conn.execute(text("CREATE FUNCTION owner_only() RETURNS int LANGUAGE sql AS 'SELECT 1'"))
    owner.dispose()
    app = create_engine(bootstrapped.app_url)
    try:
        with app.connect() as conn:
            # Row-level security is forced: with no tenant set, nothing is visible.
            assert conn.execute(text("SELECT count(*) FROM tenants")).scalar_one() == 0
        for statement in (
            "CREATE TABLE intruder (id int)",
            "CREATE TEMP TABLE intruder (id int)",
            "SELECT owner_only()",
        ):
            with pytest.raises(ProgrammingError, match="permission denied"), app.begin() as conn:
                conn.execute(text(statement))
    finally:
        app.dispose()


def test_bootstrap_can_run_again(bootstrapped: Managed) -> None:
    bootstrap_database(bootstrapped.admin_url, OWNER_PASSWORD, APP_PASSWORD)


def test_the_cli_reads_the_admin_url_and_passwords_from_the_environment(
    managed: Managed,
    bootstrapped: Managed,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ADMIN_DATABASE_URL", managed.admin_url)
    monkeypatch.setenv("VEYL_OWNER_PASSWORD", OWNER_PASSWORD)
    monkeypatch.setenv("VEYL_APP_PASSWORD", APP_PASSWORD)
    assert cli.main(["bootstrap-database", "--migrate"]) == 0
    out = capsys.readouterr().out
    assert "veyl_owner" in out and "veyl_app" in out
    assert "migrations: at the latest revision" in out
    assert "check: veyl_app can sign in" in out
    # The API's connection string, ready to paste, with the password left as a placeholder.
    host = make_url(managed.admin_url).host
    assert f"postgresql://veyl_app:<VEYL_APP_PASSWORD>@{host}" in out
    for secret in (make_url(managed.admin_url).password, OWNER_PASSWORD, APP_PASSWORD):
        assert secret not in out


def test_the_cli_reports_an_app_password_that_does_not_work(
    managed: Managed,
    bootstrapped: Managed,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ADMIN_DATABASE_URL", managed.admin_url)
    monkeypatch.setenv("VEYL_OWNER_PASSWORD", OWNER_PASSWORD)
    monkeypatch.setenv("VEYL_APP_PASSWORD", APP_PASSWORD)
    # Someone changed the role's password elsewhere; the secret no longer matches.
    admin = create_engine(normalise_database_url(managed.admin_url))
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("ALTER ROLE veyl_app PASSWORD 'Other-Pw-9k2m4n6b8v1c3x5z'"))
    admin.dispose()
    original = bootstrap_database

    def without_password_update(*args: str) -> object:
        report = original(*args)
        with (
            create_engine(normalise_database_url(managed.admin_url))
            .connect()
            .execution_options(isolation_level="AUTOCOMMIT") as conn
        ):
            conn.execute(text("ALTER ROLE veyl_app PASSWORD 'Other-Pw-9k2m4n6b8v1c3x5z'"))
        return report

    monkeypatch.setattr(cli, "bootstrap_database", without_password_update)
    try:
        assert cli.main(["bootstrap-database"]) == 1
        err = capsys.readouterr().err
        assert "veyl_app cannot sign in" in err and "password authentication failed" in err
        assert APP_PASSWORD not in err and "Other-Pw-9k2m4n6b8v1c3x5z" not in err
    finally:
        bootstrap_database(managed.admin_url, OWNER_PASSWORD, APP_PASSWORD)
