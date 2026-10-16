import uuid
from functools import cache
from uuid import UUID

from sqlalchemy import Engine, insert

from app.auth.passwords import hash_password
from app.authz import Role
from app.db.schema import tenants, users

DEFAULT_PASSWORD = "correct-horse-battery-staple"


@cache
def _hash(password: str) -> str:
    return hash_password(password)


def create_tenant(
    owner_engine: Engine, *, slug: str | None = None, name: str = "Test Tenant"
) -> tuple[UUID, str]:
    slug = slug or f"t-{uuid.uuid4().hex[:12]}"
    with owner_engine.begin() as conn:
        tenant_id = conn.execute(
            insert(tenants).values(slug=slug, name=name).returning(tenants.c.id)
        ).scalar_one()
    return tenant_id, slug


def create_user(
    owner_engine: Engine,
    tenant_id: UUID,
    *,
    role: Role = Role.MEMBER,
    email: str | None = None,
    password: str = DEFAULT_PASSWORD,
    is_active: bool = True,
) -> tuple[UUID, str]:
    email = email or f"u-{uuid.uuid4().hex[:10]}@example.test"
    with owner_engine.begin() as conn:
        user_id = conn.execute(
            insert(users)
            .values(
                tenant_id=tenant_id,
                email=email,
                password_hash=_hash(password),
                role=role.value,
                is_active=is_active,
            )
            .returning(users.c.id)
        ).scalar_one()
    return user_id, email
