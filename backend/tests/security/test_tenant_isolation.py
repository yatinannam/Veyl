"""T1 / brief test 1 (tenant A cannot reach tenant B), user-management surface.
Phase 3 extends this module to documents and retrieval."""

import pytest
from sqlalchemy import Engine, select

from app.db.schema import users
from tests.api import API, client_for, csrf, login
from tests.conftest import DatabaseUrls
from tests.factories import create_tenant_with_admin, create_user

pytestmark = pytest.mark.integration


def test_admin_cannot_modify_users_in_another_tenant(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    a = create_tenant_with_admin(owner_engine)
    b = create_tenant_with_admin(owner_engine)
    victim, _ = create_user(owner_engine, b.tenant_id)
    with client_for(database_urls.app) as client:
        login(client, a.slug, a.admin_email)
        r = client.patch(f"{API}/users/{victim}", json={"role": "admin"}, headers=csrf(client))
    assert (r.status_code, r.json()["error"]["code"]) == (404, "not_found")
    with owner_engine.connect() as conn:
        role = conn.execute(select(users.c.role).where(users.c.id == victim)).scalar_one()
    assert role == "member"


def test_session_from_one_tenant_never_sees_another(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    a = create_tenant_with_admin(owner_engine, name="Alpha")
    create_tenant_with_admin(owner_engine, name="Beta")
    with client_for(database_urls.app) as client:
        login(client, a.slug, a.admin_email)
        assert client.get(f"{API}/tenant").json()["name"] == "Alpha"
