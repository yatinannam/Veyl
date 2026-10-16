"""T5 / brief test 5 (missing or invalid authorization context results in denial) and
T14 (fail closed when storage is unavailable)."""

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, update

from app.db.schema import users
from tests.api import API, client_for, login
from tests.conftest import DatabaseUrls
from tests.factories import create_session, create_tenant, create_user

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "cookie", [None, "", "garbage", "a" * 4000], ids=["missing", "empty", "garbage", "huge"]
)
def test_missing_or_invalid_session_is_denied(
    database_urls: DatabaseUrls, cookie: str | None
) -> None:
    with client_for(database_urls.app) as client:
        if cookie is not None:
            client.cookies.set("veyl_session", cookie)
        r = client.get(f"{API}/auth/me")
    assert (r.status_code, r.json()["error"]["code"]) == (401, "unauthenticated")


@pytest.mark.parametrize(
    "kwargs",
    [{"expires_in": timedelta(seconds=-1)}, {"revoked": True}],
    ids=["expired", "revoked"],
)
def test_dead_sessions_are_denied(
    owner_engine: Engine, database_urls: DatabaseUrls, kwargs: dict[str, Any]
) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    user_id, _ = create_user(owner_engine, tenant_id)
    token = create_session(owner_engine, tenant_id, user_id, **kwargs)
    with client_for(database_urls.app) as client:
        client.cookies.set("veyl_session", token)
        assert client.get(f"{API}/auth/me").status_code == 401


def test_deactivated_users_lose_access_on_next_request(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    user_id, email = create_user(owner_engine, tenant_id)
    with client_for(database_urls.app) as client:
        login(client, slug, email)
        assert client.get(f"{API}/auth/me").status_code == 200
        with owner_engine.begin() as conn:
            conn.execute(update(users).where(users.c.id == user_id).values(is_active=False))
        assert client.get(f"{API}/auth/me").status_code == 401


def test_database_outage_fails_closed() -> None:
    with client_for("postgresql+psycopg://veyl_app:x@127.0.0.1:1/veyl") as client:
        client.cookies.set("veyl_session", "some-token")
        r = client.get(f"{API}/auth/me")
    assert (r.status_code, r.json()["error"]["code"]) == (503, "service_unavailable")
