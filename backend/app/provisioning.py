"""Operator-only tenant provisioning. Runs with the owner role (MIGRATIONS_DATABASE_URL);
there is deliberately no API for creating tenants."""

import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Engine, insert
from sqlalchemy.exc import IntegrityError

from app.auth.passwords import MIN_PASSWORD_LENGTH, hash_password
from app.authz import Role
from app.db.schema import tenants, users

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
# Same shape the API requires for new users.
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+$")


@dataclass(frozen=True, slots=True)
class ProvisionedTenant:
    tenant_id: UUID
    admin_user_id: UUID


def provision_tenant(
    owner_engine: Engine, *, slug: str, name: str, admin_email: str, admin_password: str
) -> ProvisionedTenant:
    if not SLUG_PATTERN.fullmatch(slug):
        raise ValueError("slug must be 2-63 chars of lowercase letters, digits, and hyphens")
    if not EMAIL_PATTERN.fullmatch(admin_email.strip()) or len(admin_email) > 320:
        raise ValueError("admin email must look like name@domain")
    if len(admin_password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"admin password must be at least {MIN_PASSWORD_LENGTH} characters")
    password_hash = hash_password(admin_password)
    try:
        with owner_engine.begin() as conn:
            tenant_id = conn.execute(
                insert(tenants).values(slug=slug, name=name).returning(tenants.c.id)
            ).scalar_one()
            admin_user_id = conn.execute(
                insert(users)
                .values(
                    tenant_id=tenant_id,
                    email=admin_email.strip().lower(),
                    password_hash=password_hash,
                    role=Role.ADMIN.value,
                )
                .returning(users.c.id)
            ).scalar_one()
    except IntegrityError as exc:
        raise ValueError(f"tenant slug {slug!r} already exists") from exc
    return ProvisionedTenant(tenant_id=tenant_id, admin_user_id=admin_user_id)
