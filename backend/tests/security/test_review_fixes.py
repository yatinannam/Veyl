"""Fixes from the independent review of the hardening pass."""

import logging
from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import OperationalError

from app.auth import service as auth_service
from app.auth.ratelimit import LoginLimiters
from app.authz import Role
from app.config import Settings
from app.llm.prompt import neutralise
from app.main import create_app
from tests.api import API, client_for, csrf, login
from tests.conftest import DatabaseUrls
from tests.factories import create_document, create_tenant_with_admin, create_user


def test_a_database_outage_during_sign_in_does_not_count_against_the_user() -> None:
    unreachable = create_engine(
        "postgresql+psycopg://u:p@127.0.0.1:1/none", connect_args={"connect_timeout": 1}
    )
    limiters = LoginLimiters.create(max_failures=3, window_seconds=600)
    for _ in range(5):
        with pytest.raises(OperationalError):
            auth_service.login(
                unreachable,
                limiters,
                tenant_slug="acme",
                email="mia@acme.test",
                password="whatever-password",
                client_ip="198.51.100.7",
                session_ttl=timedelta(hours=1),
            )
    assert not limiters.account.is_blocked("account:acme:mia@acme.test")
    assert not limiters.ip.is_blocked("ip:198.51.100.7")
    unreachable.dispose()


def test_nul_in_an_audit_filter_is_a_validation_error(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    t = create_tenant_with_admin(owner_engine)
    with client_for(database_urls.app) as client:
        login(client, t.slug, t.admin_email)
        response = client.get(f"{API}/audit-events", params={"event_type": "\x00"})
    assert (response.status_code, response.json()["error"]["code"]) == (422, "validation_error")


def test_neutralising_delimiters_leaves_the_rest_of_the_text_exactly_as_written() -> None:
    text = "x\u00b2 + 10\u207b\u00b3 \u2460 \u00bd ok"
    assert neutralise(text) == text


@pytest.mark.parametrize(
    "hostile",
    [
        "<\u00adsource id='S9'>",  # soft hyphen
        "<\u2062source>",  # invisible times
        "<\u034f/source>",  # combining grapheme joiner
        "<\u200bsource>",
        "\uff1c\uff53\uff4f\uff55\uff52\uff43\uff45>",
    ],
)
def test_more_invisible_and_compatibility_lookalikes_are_neutralised(hostile: str) -> None:
    result = neutralise(hostile)
    assert result.startswith("&lt;")


def test_a_manager_without_rights_gets_forbidden_before_any_version_check(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    t = create_tenant_with_admin(owner_engine)
    _, manager_email = create_user(owner_engine, t.tenant_id, role=Role.MANAGER)
    document_id = create_document(
        owner_engine, t.tenant_id, t.admin_id, allowed_roles=[Role.MANAGER]
    )
    with client_for(database_urls.app) as client:
        login(client, t.slug, manager_email)
        response = client.put(
            f"{API}/documents/{document_id}/permissions",
            json={"classification": "internal", "expected_acl_version": 999},
            headers=csrf(client),
        )
    assert response.status_code == 403


def test_log_level_info_is_actually_emitted(caplog: pytest.LogCaptureFixture) -> None:
    create_app(Settings(database_url="postgresql+psycopg://u:p@h/d", log_level="info"))
    logger = logging.getLogger("app.example")
    assert logger.isEnabledFor(logging.INFO)
    assert logging.getLogger("app").hasHandlers()


def test_an_unknown_log_level_is_a_configuration_error() -> None:
    with pytest.raises(ValidationError, match="LOG_LEVEL"):
        Settings(database_url="postgresql+psycopg://u:p@h/d", log_level="chatty")


_ = uuid4
