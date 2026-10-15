import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from tests.conftest import DatabaseUrls

pytestmark = pytest.mark.integration


def test_app_role_has_no_elevated_privileges(database_urls: DatabaseUrls) -> None:
    engine = create_engine(database_urls.app)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb "
                "FROM pg_roles WHERE rolname = current_user"
            )
        ).one()
    engine.dispose()
    assert tuple(row) == (False, False, False, False)


def test_app_role_cannot_create_tables(database_urls: DatabaseUrls) -> None:
    engine = create_engine(database_urls.app)
    with pytest.raises(ProgrammingError), engine.begin() as conn:
        conn.execute(text("CREATE TABLE should_fail (id int)"))
    engine.dispose()


def test_owner_role_cannot_bypass_rls(database_urls: DatabaseUrls) -> None:
    engine = create_engine(database_urls.owner)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
    engine.dispose()
    assert tuple(row) == (False, False)


def test_required_extensions_installed(database_urls: DatabaseUrls) -> None:
    engine = create_engine(database_urls.app)
    with engine.connect() as conn:
        names = set(conn.execute(text("SELECT extname FROM pg_extension")).scalars())
    engine.dispose()
    assert {"vector", "citext"} <= names


def test_app_role_cannot_execute_owner_functions_by_default(database_urls: DatabaseUrls) -> None:
    # Phase 2 adds SECURITY DEFINER helpers; only explicitly granted ones may be callable.
    engine = create_engine(database_urls.owner)
    with engine.connect() as conn, conn.begin() as tx:
        conn.execute(
            text(
                "CREATE FUNCTION probe_default_execute() RETURNS int "
                "LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'"
            )
        )
        allowed = conn.execute(
            text("SELECT has_function_privilege('veyl_app', 'probe_default_execute()', 'EXECUTE')")
        ).scalar_one()
        tx.rollback()
    engine.dispose()
    assert allowed is False


def test_app_role_cannot_create_temp_tables(database_urls: DatabaseUrls) -> None:
    # Temp tables can shadow real tables inside SECURITY DEFINER functions via pg_temp.
    engine = create_engine(database_urls.app)
    with engine.connect() as conn:
        allowed = conn.execute(
            text("SELECT has_database_privilege(current_user, current_database(), 'TEMP')")
        ).scalar_one()
    engine.dispose()
    assert allowed is False
