"""One-time setup of a managed Postgres (such as Neon) where the admin role can create
roles but is not a superuser. It establishes the same two roles and privileges as
`docker/postgres/init/01-roles.sh` does for the Compose database, so the migrations and
every guarantee about the app role are identical in both places."""

from dataclasses import dataclass, field

from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.db.engine import normalise_database_url

OWNER = "veyl_owner"
APP = "veyl_app"
_LIMITED = "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"


@dataclass
class BootstrapReport:
    done: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _role_exists(conn: Connection, name: str) -> bool:
    row = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :n"), {"n": name}).first()
    return row is not None


def _upsert_role(conn: Connection, name: str, password: str, attributes: str) -> None:
    """Create the role, or on a re-run only update its password (restating attributes
    such as NOSUPERUSER needs a superuser). Either way, verify it is least-privilege."""
    # Postgres quotes the name and password itself (format %I and %L); nothing is
    # interpolated by Python.
    template = (
        "ALTER ROLE %I LOGIN PASSWORD %L"
        if _role_exists(conn, name)
        else "CREATE ROLE %I LOGIN PASSWORD %L " + attributes
    )
    statement = conn.execute(
        text("SELECT format(CAST(:template AS text), CAST(:name AS text), CAST(:pw AS text))"),
        {"template": template, "name": name, "pw": password},
    ).scalar_one()
    conn.execute(text(statement))
    flags = conn.execute(
        text(
            "SELECT rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb "
            "FROM pg_roles WHERE rolname = :n"
        ),
        {"n": name},
    ).scalar_one()
    if flags:
        raise RuntimeError(f"role {name} exists with privileges it must not have")


def bootstrap_database(admin_url: str, owner_password: str, app_password: str) -> BootstrapReport:
    report = BootstrapReport()
    url = make_url(normalise_database_url(admin_url))
    admin = create_engine(url)
    try:
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            _upsert_role(conn, OWNER, owner_password, _LIMITED)
            _upsert_role(conn, APP, app_password, f"{_LIMITED} NOINHERIT")
            report.done.append(f"roles {OWNER} and {APP}")
            for extension in ("vector", "citext"):
                conn.execute(text(f"CREATE EXTENSION IF NOT EXISTS {extension}"))
            report.done.append("extensions vector and citext")
            db = conn.execute(text("SELECT quote_ident(:d)"), {"d": url.database}).scalar_one()
            conn.execute(text(f"REVOKE ALL ON DATABASE {db} FROM PUBLIC"))
            conn.execute(text(f"GRANT CONNECT, TEMPORARY ON DATABASE {db} TO {OWNER}"))
            # No TEMPORARY for the app role: temp tables can shadow real tables inside
            # SECURITY DEFINER functions.
            conn.execute(text(f"GRANT CONNECT ON DATABASE {db} TO {APP}"))
            conn.execute(text("REVOKE ALL ON SCHEMA public FROM PUBLIC"))
            conn.execute(text(f"GRANT USAGE, CREATE ON SCHEMA public TO {OWNER}"))
            conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP}"))
            report.done.append(f"database and schema privileges on {url.database}")
            for maintenance in ("postgres", "template1"):
                try:
                    conn.execute(text(f"REVOKE CONNECT ON DATABASE {maintenance} FROM PUBLIC"))
                    report.done.append(f"PUBLIC may not connect to {maintenance}")
                except DBAPIError:
                    report.skipped.append(
                        f"connect on {maintenance}: managed by the provider, not changeable "
                        "by the admin role"
                    )
    finally:
        admin.dispose()

    # Default privileges are set by the role that will own the functions.
    owner = create_engine(url.set(username=OWNER, password=owner_password))
    try:
        with owner.begin() as conn:
            # Global (no IN SCHEMA): a per-schema REVOKE cannot remove the built-in
            # PUBLIC EXECUTE default.
            conn.execute(text("ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"))
        report.done.append("functions the owner creates are not executable by default")
    finally:
        owner.dispose()
    return report
