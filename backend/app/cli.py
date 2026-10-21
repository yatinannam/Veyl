"""Operator commands: python -m app.cli <command>."""

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlencode

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError

from app.bootstrap import APP, OWNER, bootstrap_database
from app.db.diagnose import describe_db_error
from app.db.engine import create_db_engine, normalise_database_url
from app.demo.http import DemoApiError, UrllibTransport
from app.demo.reset import DemoMissing, reset_demo
from app.demo.seed import (
    AlreadySeeded,
    DemoNotReady,
    DemoStillProcessing,
    seed_demo,
    wait_until_ready,
)
from app.provisioning import provision_tenant


def _provision(engine: Engine, args: argparse.Namespace, password: str) -> int:
    try:
        result = provision_tenant(
            engine,
            slug=args.slug,
            name=args.name,
            admin_email=args.admin_email,
            admin_password=password,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"tenant_id={result.tenant_id} admin_user_id={result.admin_user_id}")
    return 0


# A hosted API that sleeps (Render's free plan) can take about a minute to answer.
API_TIMEOUT_SECONDS = 120.0


def _reset_demo(engine: Engine, args: argparse.Namespace, password: str) -> int:
    def new_transport() -> UrllibTransport:
        return UrllibTransport(args.api_url, timeout=API_TIMEOUT_SECONDS)

    try:
        report = reset_demo(engine, new_transport, password)
        print(
            f"Reset the demo: {report.people_restored} people restored, "
            f"{report.people_removed} added people removed, "
            f"{report.documents_removed} documents removed."
        )
        if report.documents_reuploaded:
            print("Uploaded again: " + ", ".join(report.documents_reuploaded))
            wait_until_ready(new_transport, password, timeout=args.wait)
    except DemoMissing as exc:
        print(
            f"error: the demo company {exc} does not exist; run seed-demo first.", file=sys.stderr
        )
        return 1
    except DemoStillProcessing as exc:
        print(f"Reset done; the worker is {exc}.")
    except (DemoApiError, DemoNotReady, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("Demo ready.")
    return 0


def _seed_demo(engine: Engine, args: argparse.Namespace, password: str) -> int:
    def new_transport() -> UrllibTransport:
        return UrllibTransport(args.api_url, timeout=API_TIMEOUT_SECONDS)

    still_processing: str | None = None
    try:
        summary = seed_demo(engine, new_transport, password)
        print("Created the demo; waiting for documents to be processed...")
        try:
            wait_until_ready(new_transport, password, timeout=args.wait)
        except DemoStillProcessing as exc:
            still_processing = str(exc)
    except AlreadySeeded as exc:
        print(
            f"Demo already seeded ({exc}). Run reset-demo to restore it, or locally "
            "`docker compose down -v` to start over.",
            file=sys.stderr,
        )
        return 1
    except (DemoApiError, DemoNotReady, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(
            "The demo may be incomplete. Reset with `docker compose down -v`, start the stack "
            "again, and rerun the seed.",
            file=sys.stderr,
        )
        return 1
    if still_processing:
        print(
            f"Everything is created; the worker is {still_processing}. "
            "Those documents become searchable when it finishes."
        )
    print(f"Demo ready: {', '.join(summary.tenants)}. Sign in as any of:")
    for email in summary.users:
        print(f"  {email}")
    return 0


BACKEND_DIR = Path(__file__).resolve().parents[1]


def _bootstrap(migrate: bool) -> int:
    names = ("ADMIN_DATABASE_URL", "VEYL_OWNER_PASSWORD", "VEYL_APP_PASSWORD")
    values = {name: os.environ.get(name, "") for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        print(f"Set {', '.join(missing)}; secrets are never taken as arguments.", file=sys.stderr)
        return 2
    try:
        report = bootstrap_database(
            values["ADMIN_DATABASE_URL"],
            values["VEYL_OWNER_PASSWORD"],
            values["VEYL_APP_PASSWORD"],
        )
    except Exception as exc:  # report the cause without echoing any URL or password
        print(f"error: bootstrap failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    for item in report.done:
        print(f"done: {item}")
    for item in report.skipped:
        print(f"skipped: {item}")
    admin = make_url(normalise_database_url(values["ADMIN_DATABASE_URL"]))
    if migrate:
        owner = admin.set(username=OWNER, password=values["VEYL_OWNER_PASSWORD"])
        try:
            _run_migrations(owner.render_as_string(hide_password=False))
        except Exception as exc:
            print(f"error: migrations failed ({type(exc).__name__})", file=sys.stderr)
            return 1
        print("migrations: at the latest revision")
    # Sign in exactly as the API will, so a wrong password shows up here, not on the host.
    app_url = admin.set(username=APP, password=values["VEYL_APP_PASSWORD"])
    try:
        check = create_db_engine(
            app_url.render_as_string(hide_password=False), connect_timeout_seconds=15
        )
        with check.connect() as conn:
            conn.execute(text("SELECT 1"))
        check.dispose()
    except SQLAlchemyError as exc:
        secrets = [values["VEYL_APP_PASSWORD"], values["VEYL_OWNER_PASSWORD"], admin.password]
        print(f"error: {APP} cannot sign in ({describe_db_error(exc, secrets)})", file=sys.stderr)
        return 1
    print(f"check: {APP} can sign in with VEYL_APP_PASSWORD")
    print("API connection string (put the app password in place of the placeholder):")
    print(f"  {_url_template(admin, APP, '<VEYL_APP_PASSWORD>')}")
    return 0


def _run_migrations(owner_url: str) -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    # Config values pass through configparser interpolation.
    config.set_main_option("sqlalchemy.url", owner_url.replace("%", "%%"))
    command.upgrade(config, "head")


def _url_template(admin: URL, user: str, password: str) -> str:
    port = f":{admin.port}" if admin.port else ""
    query = urlencode(
        [
            (k, v)
            for k, values in admin.query.items()
            for v in ([values] if isinstance(values, str) else values)
        ]
    )
    suffix = f"?{query}" if query else ""
    return f"postgresql://{user}:{password}@{admin.host}{port}/{admin.database}{suffix}"


def _owner_url() -> str | None:
    """The owner's connection string. On a hosted database the admin URL supplies only the
    host, database, and options; the connection itself is made as the owner."""
    url = os.environ.get("MIGRATIONS_DATABASE_URL")
    if url:
        return url
    admin = os.environ.get("ADMIN_DATABASE_URL")
    owner_password = os.environ.get("VEYL_OWNER_PASSWORD")
    if not (admin and owner_password):
        return None
    owner = make_url(normalise_database_url(admin)).set(username=OWNER, password=owner_password)
    return owner.render_as_string(hide_password=False)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli", description="Veyl operator commands."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    provision = commands.add_parser(
        "provision-tenant",
        help="Create a tenant and its first admin. Password comes from VEYL_ADMIN_PASSWORD.",
    )
    provision.add_argument("--slug", required=True)
    provision.add_argument("--name", required=True)
    provision.add_argument("--admin-email", required=True)
    seed = commands.add_parser(
        "seed-demo",
        help="Create the Acme and Globex demo through the API. Password from VEYL_DEMO_PASSWORD.",
    )
    seed.add_argument("--api-url", required=True, help="e.g. http://api:8000")
    seed.add_argument("--wait", type=float, default=300.0, help="seconds to wait for ingestion")
    reset = commands.add_parser(
        "reset-demo",
        help="Return the demo to its seeded state (nightly). Password from VEYL_DEMO_PASSWORD.",
    )
    reset.add_argument("--api-url", required=True, help="e.g. http://api:8000")
    reset.add_argument("--wait", type=float, default=300.0, help="seconds to wait for ingestion")
    bootstrap = commands.add_parser(
        "bootstrap-database",
        help=(
            "One-time setup of a managed Postgres: roles and privileges. Reads "
            "ADMIN_DATABASE_URL, VEYL_OWNER_PASSWORD, VEYL_APP_PASSWORD."
        ),
    )
    bootstrap.add_argument(
        "--migrate", action="store_true", help="then run the migrations as the owner role"
    )
    args = parser.parse_args(argv)
    if args.command == "bootstrap-database":
        return _bootstrap(args.migrate)

    password_env = (
        "VEYL_ADMIN_PASSWORD" if args.command == "provision-tenant" else "VEYL_DEMO_PASSWORD"
    )
    database_url = _owner_url()
    password = os.environ.get(password_env)
    if not database_url:
        print(
            "Set MIGRATIONS_DATABASE_URL (owner role), or ADMIN_DATABASE_URL and "
            "VEYL_OWNER_PASSWORD to connect as the owner on the same host.",
            file=sys.stderr,
        )
        return 2
    if not password:
        print(
            f"{password_env} must be set; passwords are never taken as arguments.",
            file=sys.stderr,
        )
        return 2

    engine = create_db_engine(database_url)
    try:
        if args.command == "provision-tenant":
            return _provision(engine, args, password)
        if args.command == "reset-demo":
            return _reset_demo(engine, args, password)
        return _seed_demo(engine, args, password)
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
