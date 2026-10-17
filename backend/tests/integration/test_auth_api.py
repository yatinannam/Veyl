import pytest
from sqlalchemy import Engine

from app.authz import Role
from tests.api import API, client_for, csrf, login
from tests.conftest import DatabaseUrls
from tests.factories import create_tenant, create_user

pytestmark = pytest.mark.integration


def test_login_returns_identity_and_permission_hints(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    tenant_id, slug = create_tenant(owner_engine, name="Acme")
    user_id, email = create_user(owner_engine, tenant_id, role=Role.MANAGER)
    with client_for(database_urls.app) as client:
        r = login(client, slug, email)
    assert r.status_code == 200
    body = r.json()
    assert body["user"] == {"id": str(user_id), "email": email, "role": "manager"}
    assert body["tenant"] == {"id": str(tenant_id), "slug": slug, "name": "Acme"}
    assert body["permissions"] == ["document.upload", "rag.query", "tenant.read", "user.directory"]


def test_me_returns_the_current_identity(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    user_id, email = create_user(owner_engine, tenant_id)
    with client_for(database_urls.app) as client:
        login(client, slug, email)
        r = client.get(f"{API}/auth/me")
    assert r.status_code == 200
    assert r.json()["user"]["id"] == str(user_id)


def test_login_normalises_email_and_slug(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    _, email = create_user(owner_engine, tenant_id)
    with client_for(database_urls.app) as client:
        r = login(client, f" {slug.upper()} ", f"  {email.upper()} ")
    assert r.status_code == 200


def test_logout_revokes_the_session(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    _, email = create_user(owner_engine, tenant_id)
    with client_for(database_urls.app) as client:
        login(client, slug, email)
        old_token = client.cookies.get("veyl_session")
        assert client.post(f"{API}/auth/logout", headers=csrf(client)).status_code == 204
        client.cookies.set("veyl_session", old_token or "")
        assert client.get(f"{API}/auth/me").status_code == 401
