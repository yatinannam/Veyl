"""T11 (insecure document upload) and T4 (forged tenant identifiers on upload)."""

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, select

from app.db.schema import documents
from tests.api import client_for, login, upload
from tests.conftest import DatabaseUrls
from tests.factories import SeededTenant, create_tenant_with_admin

pytestmark = pytest.mark.integration


@pytest.fixture
def seeded(owner_engine: Engine) -> SeededTenant:
    return create_tenant_with_admin(owner_engine)


def _document_count(owner_engine: Engine, tenant_id: UUID) -> int:
    with owner_engine.connect() as conn:
        return int(
            conn.execute(
                select(func.count())
                .select_from(documents)
                .where(documents.c.tenant_id == tenant_id)
            ).scalar_one()
        )


def test_oversized_upload_is_rejected_and_nothing_is_stored(
    owner_engine: Engine, database_urls: DatabaseUrls, seeded: SeededTenant, tmp_path: Path
) -> None:
    with client_for(database_urls.app, upload_dir=str(tmp_path), max_upload_mb=1) as client:
        login(client, seeded.slug, seeded.admin_email)
        r = upload(client, "big.txt", b"x" * (2 * 1024 * 1024))
    assert r.status_code == 413
    assert not any(p.is_file() for p in tmp_path.rglob("*"))
    assert _document_count(owner_engine, seeded.tenant_id) == 0


@pytest.mark.parametrize(
    ("filename", "data"),
    [
        ("run.exe", b"MZ"),
        ("fake.pdf", b"not a pdf"),
        ("nul.txt", b"a\x00b"),
        ("latin.txt", b"\xe9t\xe9"),
    ],
)
def test_disallowed_files_are_rejected(
    owner_engine: Engine,
    database_urls: DatabaseUrls,
    seeded: SeededTenant,
    tmp_path: Path,
    filename: str,
    data: bytes,
) -> None:
    with client_for(database_urls.app, upload_dir=str(tmp_path)) as client:
        login(client, seeded.slug, seeded.admin_email)
        r = upload(client, filename, data)
    assert (r.status_code, r.json()["error"]["code"]) == (422, "upload_rejected")
    assert _document_count(owner_engine, seeded.tenant_id) == 0


def test_hostile_filename_is_metadata_only(
    database_urls: DatabaseUrls, seeded: SeededTenant, tmp_path: Path
) -> None:
    with client_for(database_urls.app, upload_dir=str(tmp_path / "store")) as client:
        login(client, seeded.slug, seeded.admin_email)
        r = upload(client, "../../../escaped.txt", b"content")
    assert r.status_code == 202
    assert r.json()["original_filename"] == "escaped.txt"
    stored = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert stored == [tmp_path / "store" / str(seeded.tenant_id) / r.json()["id"]]


def test_forged_tenant_field_is_ignored(
    owner_engine: Engine, database_urls: DatabaseUrls, seeded: SeededTenant, tmp_path: Path
) -> None:
    other = create_tenant_with_admin(owner_engine)
    with client_for(database_urls.app, upload_dir=str(tmp_path)) as client:
        login(client, seeded.slug, seeded.admin_email)
        r = upload(
            client,
            "a.txt",
            b"text",
            extra={"tenant_id": str(other.tenant_id), "uploaded_by": str(other.admin_id)},
        )
    assert r.status_code == 202
    assert r.json()["uploaded_by"] == str(seeded.admin_id)
    assert _document_count(owner_engine, other.tenant_id) == 0
    assert _document_count(owner_engine, seeded.tenant_id) == 1


def test_sharing_an_upload_with_too_many_people_is_rejected(
    owner_engine: Engine,
    database_urls: DatabaseUrls,
    seeded: SeededTenant,
    tmp_path: Path,
) -> None:
    with client_for(database_urls.app, upload_dir=str(tmp_path)) as client:
        login(client, seeded.slug, seeded.admin_email)
        r = upload(client, "a.txt", b"hello", user_ids=[uuid4() for _ in range(501)])
    assert (r.status_code, r.json()["error"]["code"]) == (422, "upload_rejected")
    assert _document_count(owner_engine, seeded.tenant_id) == 0
