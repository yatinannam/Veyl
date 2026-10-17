import threading
from uuid import UUID

import pytest
from sqlalchemy import Engine, select

from app.authz import AuthContext, Role
from app.db.schema import sessions, tenants, users
from app.errors import Conflict
from app.users import service as user_service
from tests.api import API, client_for, csrf, login
from tests.conftest import DatabaseUrls
from tests.factories import create_tenant_with_admin, create_user

pytestmark = pytest.mark.integration

NEW_PASSWORD = "another-long-password"


def _policy_version(owner_engine: Engine, tenant_id: UUID) -> int:
    with owner_engine.connect() as conn:
        return int(
            conn.execute(
                select(tenants.c.policy_version).where(tenants.c.id == tenant_id)
            ).scalar_one()
        )


def test_admin_lists_only_own_tenant_users(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    a = create_tenant_with_admin(owner_engine)
    b = create_tenant_with_admin(owner_engine)
    member_a, _ = create_user(owner_engine, a.tenant_id)
    with client_for(database_urls.app) as client:
        login(client, a.slug, a.admin_email)
        ids = {u["id"] for u in client.get(f"{API}/users").json()}
    assert ids == {str(a.admin_id), str(member_a)}
    assert str(b.admin_id) not in ids


def test_admin_creates_a_user_who_can_log_in(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    a = create_tenant_with_admin(owner_engine)
    with client_for(database_urls.app) as admin:
        login(admin, a.slug, a.admin_email)
        r = admin.post(
            f"{API}/users",
            json={"email": "New.Person@Acme.test", "password": NEW_PASSWORD, "role": "member"},
            headers=csrf(admin),
        )
    assert r.status_code == 201
    assert r.json()["email"] == "new.person@acme.test"
    with client_for(database_urls.app) as newbie:
        assert login(newbie, a.slug, "new.person@acme.test", NEW_PASSWORD).status_code == 200


def test_create_user_rejects_case_insensitive_duplicate(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    a = create_tenant_with_admin(owner_engine)
    with client_for(database_urls.app) as admin:
        login(admin, a.slug, a.admin_email)
        r = admin.post(
            f"{API}/users",
            json={"email": a.admin_email.upper(), "password": NEW_PASSWORD, "role": "member"},
            headers=csrf(admin),
        )
    assert (r.status_code, r.json()["error"]["code"]) == (409, "conflict")


def test_create_user_rejects_short_password(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    a = create_tenant_with_admin(owner_engine)
    with client_for(database_urls.app) as admin:
        login(admin, a.slug, a.admin_email)
        r = admin.post(
            f"{API}/users",
            json={"email": "x@y.test", "password": "short", "role": "member"},
            headers=csrf(admin),
        )
    assert r.status_code == 422


def test_update_requires_at_least_one_field(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    a = create_tenant_with_admin(owner_engine)
    member, _ = create_user(owner_engine, a.tenant_id)
    with client_for(database_urls.app) as admin:
        login(admin, a.slug, a.admin_email)
        assert admin.patch(f"{API}/users/{member}", json={}, headers=csrf(admin)).status_code == 422


def test_role_change_bumps_policy_version(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    a = create_tenant_with_admin(owner_engine)
    member, _ = create_user(owner_engine, a.tenant_id)
    before = _policy_version(owner_engine, a.tenant_id)
    with client_for(database_urls.app) as admin:
        login(admin, a.slug, a.admin_email)
        r = admin.patch(f"{API}/users/{member}", json={"role": "manager"}, headers=csrf(admin))
    assert r.status_code == 200
    assert r.json()["role"] == "manager"
    assert _policy_version(owner_engine, a.tenant_id) == before + 1


def test_deactivation_revokes_sessions(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    a = create_tenant_with_admin(owner_engine)
    member, member_email = create_user(owner_engine, a.tenant_id)
    with client_for(database_urls.app) as admin, client_for(database_urls.app) as target:
        login(target, a.slug, member_email)
        login(admin, a.slug, a.admin_email)
        r = admin.patch(f"{API}/users/{member}", json={"is_active": False}, headers=csrf(admin))
        assert r.status_code == 200
        assert target.get(f"{API}/auth/me").status_code == 401
    with owner_engine.connect() as conn:
        revoked = (
            conn.execute(select(sessions.c.revoked_at).where(sessions.c.user_id == member))
            .scalars()
            .all()
        )
    assert revoked
    assert all(r is not None for r in revoked)


def test_tenant_rename(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    a = create_tenant_with_admin(owner_engine)
    with client_for(database_urls.app) as admin:
        login(admin, a.slug, a.admin_email)
        r = admin.patch(f"{API}/tenant", json={"name": "Renamed"}, headers=csrf(admin))
        assert r.status_code == 200
        assert admin.get(f"{API}/tenant").json()["name"] == "Renamed"


def test_concurrent_mutual_demotion_keeps_one_admin(
    owner_engine: Engine, app_engine: Engine
) -> None:
    a = create_tenant_with_admin(owner_engine)
    second_admin, _ = create_user(owner_engine, a.tenant_id, role=Role.ADMIN)
    session = UUID(int=0)
    ctx_a = AuthContext(
        user_id=a.admin_id, tenant_id=a.tenant_id, role=Role.ADMIN, session_id=session
    )
    ctx_b = AuthContext(
        user_id=second_admin, tenant_id=a.tenant_id, role=Role.ADMIN, session_id=session
    )
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def demote(ctx: AuthContext, target: UUID) -> None:
        barrier.wait()
        try:
            user_service.update_user(app_engine, ctx, target, role=Role.MEMBER, is_active=None)
            outcomes.append("ok")
        except Conflict:
            outcomes.append("conflict")

    threads = [
        threading.Thread(target=demote, args=(ctx_a, second_admin)),
        threading.Thread(target=demote, args=(ctx_b, a.admin_id)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["conflict", "ok"]
    with owner_engine.connect() as conn:
        admins = (
            conn.execute(
                select(users.c.id).where(
                    users.c.tenant_id == a.tenant_id, users.c.role == "admin", users.c.is_active
                )
            )
            .scalars()
            .all()
        )
    assert len(admins) == 1


def test_managers_see_the_people_directory(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    a = create_tenant_with_admin(owner_engine)
    b = create_tenant_with_admin(owner_engine)
    manager, manager_email = create_user(owner_engine, a.tenant_id, role=Role.MANAGER)
    member, member_email = create_user(owner_engine, a.tenant_id)
    create_user(owner_engine, a.tenant_id, is_active=False)
    with client_for(database_urls.app) as client:
        login(client, a.slug, manager_email)
        listed = client.get(f"{API}/users/directory").json()
    assert {(e["id"], e["role"]) for e in listed} == {
        (str(a.admin_id), "admin"),
        (str(manager), "manager"),
        (str(member), "member"),
    }
    assert str(b.admin_id) not in {e["id"] for e in listed}
    with client_for(database_urls.app) as client:
        login(client, a.slug, member_email)
        assert client.get(f"{API}/users/directory").status_code == 403
