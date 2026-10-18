"""Operator commands: python -m app.cli <command>."""

import argparse
import os
import sys
from collections.abc import Sequence

from sqlalchemy import Engine, create_engine

from app.demo.http import DemoApiError, UrllibTransport
from app.demo.seed import AlreadySeeded, DemoNotReady, seed_demo, wait_until_ready
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


def _seed_demo(engine: Engine, args: argparse.Namespace, password: str) -> int:
    def new_transport() -> UrllibTransport:
        return UrllibTransport(args.api_url)

    try:
        summary = seed_demo(engine, new_transport, password)
        print("Created the demo; waiting for documents to be processed...")
        wait_until_ready(new_transport, password, timeout=args.wait)
    except AlreadySeeded as exc:
        print(
            f"Demo already seeded ({exc}). Reset with `docker compose down -v` to start over.",
            file=sys.stderr,
        )
        return 1
    except (DemoApiError, DemoNotReady, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Demo ready: {', '.join(summary.tenants)}. Sign in as any of:")
    for email in summary.users:
        print(f"  {email}")
    return 0


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
    args = parser.parse_args(argv)

    password_env = (
        "VEYL_ADMIN_PASSWORD" if args.command == "provision-tenant" else "VEYL_DEMO_PASSWORD"
    )
    database_url = os.environ.get("MIGRATIONS_DATABASE_URL")
    password = os.environ.get(password_env)
    if not database_url:
        print("MIGRATIONS_DATABASE_URL must be set (owner role).", file=sys.stderr)
        return 2
    if not password:
        print(
            f"{password_env} must be set; passwords are never taken as arguments.",
            file=sys.stderr,
        )
        return 2

    engine = create_engine(database_url)
    try:
        if args.command == "provision-tenant":
            return _provision(engine, args, password)
        return _seed_demo(engine, args, password)
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
