from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, text, update

from app.auth.tokens import hash_token
from app.authz import Role
from app.db.schema import users
from tests.factories import create_session, create_tenant, create_user

pytestmark = pytest.mark.integration

DEFINER_FUNCTIONS_FOR_APP = {"auth_resolve_login", "auth_resolve_session"}


def _login_row(app_engine: Engine, slug: str, email: str) -> Any:
    with app_engine.connect() as conn:
        return conn.execute(
            text("SELECT * FROM auth_resolve_login(:slug, :email)"),
            {"slug": slug, "email": email},
        ).one_or_none()


def _session_row(app_engine: Engine, token: str) -> Any:
    with app_engine.connect() as conn:
        return conn.execute(
            text("SELECT * FROM auth_resolve_session(:h)"), {"h": hash_token(token)}
        ).one_or_none()


def test_resolve_login_matches_email_case_insensitively(
    owner_engine: Engine, app_engine: Engine
) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    user_id, email = create_user(owner_engine, tenant_id, role=Role.MANAGER)
    row = _login_row(app_engine, slug, email.upper())
    assert row is not None
    assert (row.user_id, row.tenant_id, row.role, row.is_active) == (
        user_id,
        tenant_id,
        "manager",
        True,
    )
    assert row.password_hash.startswith("$argon2id$")


def test_resolve_login_is_scoped_to_the_named_tenant(
    owner_engine: Engine, app_engine: Engine
) -> None:
    _, slug_a = create_tenant(owner_engine)
    tenant_b, _ = create_tenant(owner_engine)
    _, email_b = create_user(owner_engine, tenant_b)
    assert _login_row(app_engine, slug_a, email_b) is None


def test_resolve_session_returns_live_sessions(owner_engine: Engine, app_engine: Engine) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    user_id, _ = create_user(owner_engine, tenant_id, role=Role.ADMIN)
    row = _session_row(app_engine, create_session(owner_engine, tenant_id, user_id))
    assert row is not None
    assert (row.user_id, row.tenant_id, row.role) == (user_id, tenant_id, "admin")


@pytest.mark.parametrize(
    "kwargs",
    [{"revoked": True}, {"expires_in": timedelta(seconds=-1)}],
    ids=["revoked", "expired"],
)
def test_resolve_session_rejects_dead_sessions(
    owner_engine: Engine, app_engine: Engine, kwargs: dict[str, Any]
) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    user_id, _ = create_user(owner_engine, tenant_id)
    token = create_session(owner_engine, tenant_id, user_id, **kwargs)
    assert _session_row(app_engine, token) is None


def test_resolve_session_rejects_inactive_users(owner_engine: Engine, app_engine: Engine) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    user_id, _ = create_user(owner_engine, tenant_id)
    token = create_session(owner_engine, tenant_id, user_id)
    with owner_engine.begin() as conn:
        conn.execute(update(users).where(users.c.id == user_id).values(is_active=False))
    assert _session_row(app_engine, token) is None


def test_resolve_session_rejects_unknown_tokens(app_engine: Engine) -> None:
    assert _session_row(app_engine, "never-issued") is None


def _definer_functions(owner_engine: Engine) -> list[Any]:
    with owner_engine.connect() as conn:
        return list(
            conn.execute(
                text(
                    "SELECT p.proname, p.proconfig, "
                    "has_function_privilege('veyl_app', p.oid, 'EXECUTE') AS app_can_execute "
                    "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'public' AND p.prosecdef"
                )
            ).all()
        )


def test_security_definer_functions_pin_search_path(owner_engine: Engine) -> None:
    rows = _definer_functions(owner_engine)
    assert {r.proname for r in rows} >= DEFINER_FUNCTIONS_FOR_APP
    for r in rows:
        assert r.proconfig is not None, r.proname
        assert "search_path=public, pg_temp" in r.proconfig, r.proname


def test_app_role_executes_only_the_intended_definer_functions(owner_engine: Engine) -> None:
    executable = {r.proname for r in _definer_functions(owner_engine) if r.app_can_execute}
    assert executable == DEFINER_FUNCTIONS_FOR_APP
