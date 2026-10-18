"""Text the database cannot store (NUL characters) is rejected at the edge with a 422,
never a 500 from deep inside a transaction."""

from pathlib import Path

import pytest
from sqlalchemy import Engine

from tests.api import API, client_for, csrf, login, upload
from tests.conftest import DatabaseUrls
from tests.factories import SeededTenant, create_tenant_with_admin


@pytest.fixture
def seeded(owner_engine: Engine) -> SeededTenant:
    return create_tenant_with_admin(owner_engine)


def _code(response: object) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]  # type: ignore[attr-defined]


@pytest.mark.parametrize("field", ["tenant_slug", "email"])
def test_nul_in_login_fields_is_a_validation_error(
    database_urls: DatabaseUrls, seeded: SeededTenant, field: str
) -> None:
    body = {"tenant_slug": seeded.slug, "email": seeded.admin_email, "password": "x" * 12}
    body[field] = body[field] + "\x00"
    with client_for(database_urls.app) as client:
        response = client.post(f"{API}/auth/login", json=body)
    assert _code(response) == (422, "validation_error")


def test_nul_in_an_upload_title_is_a_validation_error(
    database_urls: DatabaseUrls, seeded: SeededTenant, tmp_path: Path
) -> None:
    with client_for(database_urls.app, upload_dir=str(tmp_path)) as client:
        login(client, seeded.slug, seeded.admin_email)
        response = upload(client, "a.txt", b"hello", title="Bad\x00title")
    assert _code(response) == (422, "validation_error")


def test_nul_in_a_question_or_new_user_is_a_validation_error(
    database_urls: DatabaseUrls, seeded: SeededTenant
) -> None:
    with client_for(database_urls.app) as client:
        login(client, seeded.slug, seeded.admin_email)
        question = client.post(
            f"{API}/query", json={"question": "hi\x00there"}, headers=csrf(client)
        )
        person = client.post(
            f"{API}/users",
            json={"email": "a\x00b@x.test", "password": "a-long-password", "role": "member"},
            headers=csrf(client),
        )
        rename = client.patch(f"{API}/tenant", json={"name": "Acme\x00"}, headers=csrf(client))
    assert _code(question) == (422, "validation_error")
    assert _code(person) == (422, "validation_error")
    assert _code(rename) == (422, "validation_error")
