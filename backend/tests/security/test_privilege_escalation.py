"""T10 (role escalation and privilege bypass)."""

import pytest
from sqlalchemy import Engine

from app.authz import Role
from tests.api import API, client_for, csrf, login
from tests.conftest import DatabaseUrls
from tests.factories import create_tenant_with_admin, create_user

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("role", [Role.MEMBER, Role.MANAGER])
def test_non_admins_cannot_manage_users_or_tenant(
    owner_engine: Engine, database_urls: DatabaseUrls, role: Role
) -> None:
    a = create_tenant_with_admin(owner_engine)
    user_id, email = create_user(owner_engine, a.tenant_id, role=role)
    with client_for(database_urls.app) as client:
        login(client, a.slug, email)
        h = csrf(client)
        assert client.get(f"{API}/users").status_code == 403
        new_user = {"email": "x@y.test", "password": "another-long-password", "role": "admin"}
        assert client.post(f"{API}/users", json=new_user, headers=h).status_code == 403
        r = client.patch(f"{API}/users/{user_id}", json={"role": "admin"}, headers=h)
        assert r.status_code == 403
        r = client.patch(f"{API}/tenant", json={"name": "Mine now"}, headers=h)
        assert r.status_code == 403


def test_admin_cannot_change_own_role_or_status(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    a = create_tenant_with_admin(owner_engine)
    with client_for(database_urls.app) as admin:
        login(admin, a.slug, a.admin_email)
        h = csrf(admin)
        r = admin.patch(f"{API}/users/{a.admin_id}", json={"role": "member"}, headers=h)
        assert r.status_code == 403
        r = admin.patch(f"{API}/users/{a.admin_id}", json={"is_active": False}, headers=h)
        assert r.status_code == 403


def test_demotion_takes_effect_on_the_next_request(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    a = create_tenant_with_admin(owner_engine)
    other_admin, other_email = create_user(owner_engine, a.tenant_id, role=Role.ADMIN)
    with client_for(database_urls.app) as first, client_for(database_urls.app) as second:
        login(first, a.slug, a.admin_email)
        login(second, a.slug, other_email)
        assert second.get(f"{API}/users").status_code == 200
        r = first.patch(f"{API}/users/{other_admin}", json={"role": "member"}, headers=csrf(first))
        assert r.status_code == 200
        assert second.get(f"{API}/users").status_code == 403
