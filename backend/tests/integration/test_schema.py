import pytest
from sqlalchemy import Engine, func, insert, inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.schema import metadata, sessions
from tests.factories import create_tenant, create_user

pytestmark = pytest.mark.integration


def test_schema_module_matches_migrated_database(owner_engine: Engine) -> None:
    inspector = inspect(owner_engine)
    for table in metadata.sorted_tables:
        db_columns = {c["name"] for c in inspector.get_columns(table.name)}
        assert db_columns == set(table.c.keys()), table.name


def test_every_table_has_forced_row_level_security(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' "
                "AND c.relname <> 'alembic_version'"
            )
        ).all()
    assert rows, "no tables found"
    assert [r.relname for r in rows if not (r.relrowsecurity and r.relforcerowsecurity)] == []


def test_session_cannot_reference_a_user_in_another_tenant(owner_engine: Engine) -> None:
    tenant_a, _ = create_tenant(owner_engine)
    tenant_b, _ = create_tenant(owner_engine)
    user_a, _ = create_user(owner_engine, tenant_a)
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        conn.execute(
            insert(sessions).values(
                tenant_id=tenant_b, user_id=user_a, token_hash=b"x" * 32, expires_at=func.now()
            )
        )
