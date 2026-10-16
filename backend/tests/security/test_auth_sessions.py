"""T15 (session theft, CSRF) and T16 (brute-force login)."""

import hashlib
from typing import Any

import pytest
from sqlalchemy import Engine, select, text

from app.db.schema import sessions
from tests.api import API, client_for, csrf, login, set_cookies
from tests.conftest import DatabaseUrls
from tests.factories import DEFAULT_PASSWORD, create_tenant, create_user

pytestmark = pytest.mark.integration


def _cookie(response: Any, name: str) -> str:
    return next(c for c in set_cookies(response) if c.startswith(f"{name}="))


def test_session_cookie_is_httponly_lax_and_csrf_cookie_is_readable(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    _, email = create_user(owner_engine, tenant_id)
    with client_for(database_urls.app) as client:
        r = login(client, slug, email)
    session_cookie = _cookie(r, "veyl_session")
    csrf_cookie = _cookie(r, "veyl_csrf")
    assert "HttpOnly" in session_cookie
    assert "samesite=lax" in session_cookie.lower()
    assert "Path=/" in session_cookie
    assert "Secure" not in session_cookie
    assert "HttpOnly" not in csrf_cookie


def test_cookies_are_secure_in_production(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    _, email = create_user(owner_engine, tenant_id)
    with client_for(database_urls.app, environment="production") as client:
        r = login(client, slug, email)
    assert "Secure" in _cookie(r, "veyl_session")
    assert "Secure" in _cookie(r, "veyl_csrf")


def test_session_token_is_stored_only_as_a_sha256_hash(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    user_id, email = create_user(owner_engine, tenant_id)
    with client_for(database_urls.app) as client:
        login(client, slug, email)
        token = client.cookies.get("veyl_session") or ""
    with owner_engine.connect() as conn:
        stored = (
            conn.execute(select(sessions.c.token_hash).where(sessions.c.user_id == user_id))
            .scalars()
            .all()
        )
    assert [bytes(h) for h in stored] == [hashlib.sha256(token.encode()).digest()]


def test_session_expiry_uses_database_clock(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    user_id, email = create_user(owner_engine, tenant_id)
    with client_for(database_urls.app, session_ttl_minutes=60) as client:
        login(client, slug, email)
    with owner_engine.connect() as conn:
        seconds = conn.execute(
            text(
                "SELECT extract(epoch FROM expires_at - created_at) "
                "FROM sessions WHERE user_id = :u"
            ),
            {"u": user_id},
        ).scalar_one()
    assert abs(float(seconds) - 3600) < 5


def test_login_failures_are_indistinguishable(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    _, email = create_user(owner_engine, tenant_id)
    _, inactive = create_user(owner_engine, tenant_id, is_active=False)
    attempts = [
        (slug, email, "wrong-password-123"),
        (slug, "nobody@example.test", "whatever-password"),
        ("no-such-tenant", email, "whatever-password"),
        (slug, inactive, DEFAULT_PASSWORD),
    ]
    bodies = []
    with client_for(database_urls.app) as client:
        for s, e, p in attempts:
            r = login(client, s, e, p)
            assert r.status_code == 401
            error = r.json()["error"]
            bodies.append((error["code"], error["message"]))
    assert len(set(bodies)) == 1
    assert bodies[0][0] == "invalid_credentials"


def test_unsafe_requests_require_matching_csrf_token(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    _, email = create_user(owner_engine, tenant_id)
    with client_for(database_urls.app) as client:
        login(client, slug, email)
        missing = client.post(f"{API}/auth/logout")
        wrong = client.post(f"{API}/auth/logout", headers={"X-CSRF-Token": "not-the-token"})
        assert (missing.status_code, missing.json()["error"]["code"]) == (403, "csrf_failed")
        assert (wrong.status_code, wrong.json()["error"]["code"]) == (403, "csrf_failed")
        assert client.post(f"{API}/auth/logout", headers=csrf(client)).status_code == 204


def test_non_ascii_csrf_header_is_rejected_not_crashed(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    _, email = create_user(owner_engine, tenant_id)
    with client_for(database_urls.app) as client:
        login(client, slug, email)
        r = client.post(f"{API}/auth/logout", headers={"X-CSRF-Token": b"\xc3\xa9t\xc3\xa9"})
    assert r.status_code == 403


def test_account_is_locked_after_repeated_failures(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    _, email = create_user(owner_engine, tenant_id)
    _, other = create_user(owner_engine, tenant_id)
    with client_for(database_urls.app, login_max_failures=3) as client:
        for _ in range(3):
            assert login(client, slug, email, "wrong-password-123").status_code == 401
        locked = login(client, slug, email)
        assert (locked.status_code, locked.json()["error"]["code"]) == (429, "rate_limited")
        assert login(client, slug, other).status_code == 200


def test_oversized_passwords_are_rejected_before_hashing(database_urls: DatabaseUrls) -> None:
    with client_for(database_urls.app) as client:
        r = login(client, "acme", "a@b.test", "x" * 5000)
    assert r.status_code == 422


def test_no_cors_headers_for_foreign_origins(database_urls: DatabaseUrls) -> None:
    with client_for(database_urls.app) as client:
        r = client.options(
            f"{API}/auth/login",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
