"""Operator commands: python -m app.cli <command>."""

import argparse
import os
import sys
from collections.abc import Sequence

from sqlalchemy import create_engine

from app.provisioning import provision_tenant


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
    args = parser.parse_args(argv)

    database_url = os.environ.get("MIGRATIONS_DATABASE_URL")
    password = os.environ.get("VEYL_ADMIN_PASSWORD")
    if not database_url:
        print("MIGRATIONS_DATABASE_URL must be set (owner role).", file=sys.stderr)
        return 2
    if not password:
        print(
            "VEYL_ADMIN_PASSWORD must be set; passwords are never taken as arguments.",
            file=sys.stderr,
        )
        return 2

    engine = create_engine(database_url)
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
    finally:
        engine.dispose()
    print(f"tenant_id={result.tenant_id} admin_user_id={result.admin_user_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
