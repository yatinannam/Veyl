"""T1 (cross-tenant leakage) and T5 (missing authorization context), database layer.

RLS must hide every row when no tenant is set, show only the current tenant's rows
when one is, and refuse writes into another tenant."""

import uuid

import pytest
from sqlalchemy import Connection, Engine, Table, delete, func, insert, select, text, update
from sqlalchemy.exc import DataError, ProgrammingError

from app.db.schema import sessions, tenants, users
from app.db.tenancy import tenant_transaction
from tests.factories import create_tenant, create_user

pytestmark = pytest.mark.integration


def _count(conn: Connection, table: Table) -> int:
    return int(conn.execute(select(func.count()).select_from(table)).scalar_one())


def test_no_tenant_context_sees_no_rows(owner_engine: Engine, app_engine: Engine) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    create_user(owner_engine, tenant_id)
    with app_engine.connect() as conn:
        assert _count(conn, tenants) == 0
        assert _count(conn, users) == 0
        assert _count(conn, sessions) == 0


def test_tenant_context_sees_only_own_rows(owner_engine: Engine, app_engine: Engine) -> None:
    tenant_a, _ = create_tenant(owner_engine)
    tenant_b, _ = create_tenant(owner_engine)
    user_a, _ = create_user(owner_engine, tenant_a)
    user_b, _ = create_user(owner_engine, tenant_b)
    with tenant_transaction(app_engine, tenant_a) as conn:
        user_ids = set(conn.execute(select(users.c.id)).scalars())
        tenant_ids = set(conn.execute(select(tenants.c.id)).scalars())
    assert user_a in user_ids
    assert user_b not in user_ids
    assert tenant_ids == {tenant_a}


def test_cannot_insert_rows_into_another_tenant(owner_engine: Engine, app_engine: Engine) -> None:
    tenant_a, _ = create_tenant(owner_engine)
    tenant_b, _ = create_tenant(owner_engine)
    with pytest.raises(ProgrammingError), tenant_transaction(app_engine, tenant_a) as conn:
        conn.execute(
            insert(users).values(
                tenant_id=tenant_b, email="x@y.test", password_hash="h", role="member"
            )
        )


def test_tenant_context_does_not_leak_into_the_next_transaction(
    owner_engine: Engine, app_engine: Engine
) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    create_user(owner_engine, tenant_id)
    with tenant_transaction(app_engine, tenant_id) as conn:
        assert _count(conn, users) >= 1
    with app_engine.connect() as conn:
        assert _count(conn, users) == 0


def test_tenant_transaction_requires_a_uuid(app_engine: Engine) -> None:
    with pytest.raises(TypeError), tenant_transaction(app_engine, str(uuid.uuid4())):  # type: ignore[arg-type]
        pass


def test_malformed_tenant_setting_fails_closed(app_engine: Engine) -> None:
    with pytest.raises(DataError), app_engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', 'not-a-uuid', true)"))
        conn.execute(select(users.c.id)).all()


def test_app_role_cannot_create_tenants(app_engine: Engine) -> None:
    new_id = uuid.uuid4()
    with pytest.raises(ProgrammingError), tenant_transaction(app_engine, new_id) as conn:
        conn.execute(insert(tenants).values(id=new_id, slug=f"x-{new_id.hex[:8]}", name="X"))


def test_app_role_cannot_delete_users(owner_engine: Engine, app_engine: Engine) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    user_id, _ = create_user(owner_engine, tenant_id)
    with pytest.raises(ProgrammingError), tenant_transaction(app_engine, tenant_id) as conn:
        conn.execute(delete(users).where(users.c.id == user_id))


@pytest.mark.parametrize("column", ["tenant_id", "email", "password_hash"])
def test_app_role_cannot_rewrite_protected_user_columns(
    owner_engine: Engine, app_engine: Engine, column: str
) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    other_tenant, _ = create_tenant(owner_engine)
    user_id, _ = create_user(owner_engine, tenant_id)
    value = {"tenant_id": other_tenant, "email": "new@x.test", "password_hash": "h"}[column]
    with pytest.raises(ProgrammingError), tenant_transaction(app_engine, tenant_id) as conn:
        conn.execute(update(users).where(users.c.id == user_id).values({column: value}))
