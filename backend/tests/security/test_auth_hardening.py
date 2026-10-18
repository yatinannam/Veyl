"""Session and administration edge cases from the Phase 2 review."""

import threading
import time
from contextlib import suppress
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import Engine, select, update

from app.auth import service as auth_service
from app.auth.ratelimit import LoginLimiters
from app.authz import AuthContext, Role
from app.db.schema import sessions, users
from app.errors import Forbidden, InvalidCredentials, RateLimited
from app.provisioning import provision_tenant
from app.users import service as user_service
from tests.api import API, client_for, csrf, login, set_cookies
from tests.conftest import DatabaseUrls
from tests.factories import create_session, create_tenant, create_user


def test_a_caller_demoted_mid_request_cannot_manage_users(
    owner_engine: Engine, app_engine: Engine
) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    admin, _ = create_user(owner_engine, tenant_id, role=Role.ADMIN)
    create_user(owner_engine, tenant_id, role=Role.ADMIN)
    target, _ = create_user(owner_engine, tenant_id)
    # The request's context was built while the caller was still an admin...
    ctx = AuthContext(user_id=admin, tenant_id=tenant_id, role=Role.ADMIN, session_id=uuid4())
    # ...and another admin demotes them before the change runs.
    with owner_engine.begin() as conn:
        conn.execute(update(users).where(users.c.id == admin).values(role=Role.MEMBER.value))
    with pytest.raises(Forbidden):
        user_service.update_user(app_engine, ctx, target, role=Role.ADMIN, is_active=None)
    with pytest.raises(Forbidden):
        user_service.create_user(
            app_engine, ctx, email=f"x-{uuid4().hex[:6]}@t.test", password="a" * 12, role=Role.ADMIN
        )


def test_a_burst_of_wrong_passwords_cannot_exceed_the_account_limit(
    owner_engine: Engine, app_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    _, email = create_user(owner_engine, tenant_id)
    verified = []
    real_verify = auth_service.verify_password

    def slow_verify(stored: str | None, password: str) -> bool:
        verified.append(1)
        time.sleep(0.2)
        return real_verify(stored, password)

    monkeypatch.setattr(auth_service, "verify_password", slow_verify)
    limiters = LoginLimiters.create(max_failures=3, window_seconds=600, max_concurrent_hashes=20)

    def attempt() -> None:
        with suppress(InvalidCredentials, RateLimited):
            auth_service.login(
                app_engine,
                limiters,
                tenant_slug=slug,
                email=email,
                password="wrong-password-123",
                client_ip="203.0.113.9",
                session_ttl=timedelta(hours=1),
            )

    threads = [threading.Thread(target=attempt) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(verified) <= 3


def test_signing_in_revokes_the_session_the_browser_already_had(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    user_id, email = create_user(owner_engine, tenant_id)
    old = create_session(owner_engine, tenant_id, user_id)
    with client_for(database_urls.app) as client:
        client.cookies.set("veyl_session", old)
        assert login(client, slug, email).status_code == 200
    with owner_engine.connect() as conn:
        revoked = conn.execute(
            select(sessions.c.revoked_at).where(sessions.c.user_id == user_id)
        ).scalars()
        assert sum(r is not None for r in revoked) == 1


def test_logout_with_a_dead_session_still_clears_the_cookies(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    user_id, _ = create_user(owner_engine, tenant_id)
    dead = create_session(owner_engine, tenant_id, user_id, revoked=True)
    with client_for(database_urls.app) as client:
        client.cookies.set("veyl_session", dead)
        client.cookies.set("veyl_csrf", "csrf-value")
        response = client.post(f"{API}/auth/logout", headers=csrf(client))
    assert response.status_code == 204
    cleared = " ".join(set_cookies(response))
    assert "veyl_session=" in cleared and "veyl_csrf=" in cleared


def test_non_ascii_csrf_cookie_is_rejected_not_crashed(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    _, email = create_user(owner_engine, tenant_id)
    with client_for(database_urls.app) as client:
        login(client, slug, email)
        session = client.cookies.get("veyl_session")
        response = client.post(
            f"{API}/auth/logout",
            headers={
                "Cookie": f"veyl_session={session}; veyl_csrf=".encode() + b"\xc3\xa9t\xc3\xa9",
                "X-CSRF-Token": "plain",
            },
        )
    assert response.status_code == 403


def test_provisioning_rejects_a_malformed_admin_email(owner_engine: Engine) -> None:
    with pytest.raises(ValueError, match="email"):
        provision_tenant(
            owner_engine,
            slug=f"t-{uuid4().hex[:8]}",
            name="Bad",
            admin_email="not-an-email",
            admin_password="a-long-password",
        )
