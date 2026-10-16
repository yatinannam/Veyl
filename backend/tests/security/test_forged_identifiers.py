"""T4 / brief test 4 (manipulating client-supplied tenant IDs does not bypass isolation)."""

import pytest
from sqlalchemy import Engine

from tests.api import API, client_for, csrf, login
from tests.conftest import DatabaseUrls
from tests.factories import create_tenant_with_admin

pytestmark = pytest.mark.integration


def test_tenant_id_in_request_bodies_is_rejected(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    a = create_tenant_with_admin(owner_engine)
    b = create_tenant_with_admin(owner_engine)
    forged = str(b.tenant_id)
    with client_for(database_urls.app) as client:
        r = client.post(
            f"{API}/auth/login",
            json={
                "tenant_slug": a.slug,
                "email": a.admin_email,
                "password": "x" * 12,
                "tenant_id": forged,
            },
        )
        assert r.status_code == 422
        login(client, a.slug, a.admin_email)
        h = csrf(client)
        new_user = {
            "email": "f@x.test",
            "password": "another-long-password",
            "role": "member",
            "tenant_id": forged,
        }
        assert client.post(f"{API}/users", json=new_user, headers=h).status_code == 422
        r = client.patch(f"{API}/tenant", json={"name": "N", "id": forged}, headers=h)
        assert r.status_code == 422


def test_tenant_headers_and_query_params_do_not_change_scope(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    a = create_tenant_with_admin(owner_engine)
    b = create_tenant_with_admin(owner_engine)
    forged_headers = {"X-Tenant-ID": str(b.tenant_id), "X-User-ID": str(b.admin_id)}
    forged_params = {"tenant_id": str(b.tenant_id)}
    with client_for(database_urls.app) as client:
        login(client, a.slug, a.admin_email)
        tenant = client.get(f"{API}/tenant", headers=forged_headers, params=forged_params).json()
        listed = client.get(f"{API}/users", headers=forged_headers, params=forged_params).json()
        me = client.get(f"{API}/auth/me", headers=forged_headers).json()
    assert tenant["id"] == str(a.tenant_id)
    assert {u["id"] for u in listed} == {str(a.admin_id)}
    assert me["user"]["id"] == str(a.admin_id)
