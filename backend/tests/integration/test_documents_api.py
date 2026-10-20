from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select

from app.authz import Classification, Role
from app.db.schema import document_chunks, documents, ingestion_jobs, tenants
from tests.api import API, client_for, csrf, login, upload
from tests.conftest import DatabaseUrls
from tests.factories import (
    SeededTenant,
    add_chunks,
    create_document,
    create_tenant_with_admin,
    create_user,
    unit_vector,
)

pytestmark = pytest.mark.integration


def _policy_version(owner_engine: Engine, tenant_id: UUID) -> int:
    with owner_engine.connect() as conn:
        return int(
            conn.execute(
                select(tenants.c.policy_version).where(tenants.c.id == tenant_id)
            ).scalar_one()
        )


@pytest.fixture
def seeded(owner_engine: Engine) -> SeededTenant:
    return create_tenant_with_admin(owner_engine)


def test_manager_upload_is_stored_queued_and_owned(
    owner_engine: Engine, database_urls: DatabaseUrls, seeded: SeededTenant, tmp_path: Path
) -> None:
    manager, email = create_user(owner_engine, seeded.tenant_id, role=Role.MANAGER)
    with client_for(database_urls.app, upload_dir=str(tmp_path)) as client:
        login(client, seeded.slug, email)
        r = upload(
            client,
            "plan.txt",
            b"Roadmap for Q4",
            title="Roadmap",
            classification="confidential",
            roles=["manager"],
        )
    assert r.status_code == 202, r.text
    body = r.json()
    assert (body["status"], body["classification"], body["can_manage"]) == (
        "pending",
        "confidential",
        True,
    )
    assert body["allowed_roles"] == ["manager"]
    assert str(manager) in body["allowed_user_ids"]
    document_id = UUID(body["id"])
    assert (tmp_path / str(seeded.tenant_id) / str(document_id)).read_bytes() == b"Roadmap for Q4"
    with owner_engine.connect() as conn:
        jobs = (
            conn.execute(
                select(ingestion_jobs.c.status).where(ingestion_jobs.c.document_id == document_id)
            )
            .scalars()
            .all()
        )
    assert jobs == ["queued"]


@pytest.mark.parametrize(
    ("role", "classification", "status"),
    [
        (Role.MEMBER, "public", 403),
        (Role.MANAGER, "restricted", 403),
        (Role.MANAGER, "confidential", 202),
        (Role.ADMIN, "restricted", 202),
    ],
)
def test_upload_requires_role_and_clearance(
    owner_engine: Engine,
    database_urls: DatabaseUrls,
    seeded: SeededTenant,
    tmp_path: Path,
    role: Role,
    classification: str,
    status: int,
) -> None:
    _, email = create_user(owner_engine, seeded.tenant_id, role=role)
    with client_for(database_urls.app, upload_dir=str(tmp_path)) as client:
        login(client, seeded.slug, email)
        assert upload(client, "a.txt", b"text", classification=classification).status_code == status


def test_grants_to_users_outside_the_tenant_are_rejected(
    owner_engine: Engine, database_urls: DatabaseUrls, seeded: SeededTenant, tmp_path: Path
) -> None:
    other = create_tenant_with_admin(owner_engine)
    with client_for(database_urls.app, upload_dir=str(tmp_path)) as client:
        login(client, seeded.slug, seeded.admin_email)
        r = upload(client, "a.txt", b"text", user_ids=[other.admin_id])
    assert (r.status_code, r.json()["error"]["code"]) == (422, "unknown_users")
    assert not any(p.is_file() for p in tmp_path.rglob("*"))


def test_listing_shows_only_readable_documents_and_hides_acls_from_readers(
    owner_engine: Engine, database_urls: DatabaseUrls, seeded: SeededTenant
) -> None:
    _, member_email = create_user(owner_engine, seeded.tenant_id, role=Role.MEMBER)
    shared = create_document(
        owner_engine,
        seeded.tenant_id,
        seeded.admin_id,
        allowed_roles=[Role.MEMBER],
        title="Handbook",
    )
    create_document(
        owner_engine,
        seeded.tenant_id,
        seeded.admin_id,
        classification=Classification.RESTRICTED,
        allowed_roles=[Role.ADMIN],
        title="Board",
    )
    with client_for(database_urls.app) as client:
        login(client, seeded.slug, member_email)
        listed = client.get(f"{API}/documents").json()
    assert [d["id"] for d in listed] == [str(shared)]
    assert listed[0]["allowed_roles"] is None and listed[0]["can_manage"] is False


def test_unreadable_and_foreign_documents_are_not_found(
    owner_engine: Engine, database_urls: DatabaseUrls, seeded: SeededTenant
) -> None:
    other = create_tenant_with_admin(owner_engine)
    foreign = create_document(
        owner_engine, other.tenant_id, other.admin_id, allowed_roles=list(Role)
    )
    hidden = create_document(
        owner_engine, seeded.tenant_id, seeded.admin_id, allowed_roles=[Role.ADMIN]
    )
    _, member_email = create_user(owner_engine, seeded.tenant_id, role=Role.MEMBER)
    with client_for(database_urls.app) as client:
        login(client, seeded.slug, member_email)
        for document_id in (foreign, hidden):
            r = client.get(f"{API}/documents/{document_id}")
            assert (r.status_code, r.json()["error"]["code"]) == (404, "not_found")


def test_permission_change_rewrites_chunk_acls_and_bumps_policy_version(
    owner_engine: Engine, database_urls: DatabaseUrls, seeded: SeededTenant
) -> None:
    member, _ = create_user(owner_engine, seeded.tenant_id, role=Role.MEMBER)
    document_id = create_document(
        owner_engine, seeded.tenant_id, seeded.admin_id, allowed_roles=[Role.MEMBER]
    )
    add_chunks(owner_engine, document_id, ["a", "b"], [unit_vector(1), unit_vector(2)])
    before = _policy_version(owner_engine, seeded.tenant_id)
    with client_for(database_urls.app) as client:
        login(client, seeded.slug, seeded.admin_email)
        r = client.put(
            f"{API}/documents/{document_id}/permissions",
            json={
                "classification": "confidential",
                "allowed_roles": ["manager"],
                "allowed_user_ids": [str(member)],
            },
            headers=csrf(client),
        )
    assert r.status_code == 200, r.text
    with owner_engine.connect() as conn:
        doc = conn.execute(
            select(documents.c.acl_version, documents.c.allowed_user_ids).where(
                documents.c.id == document_id
            )
        ).one()
        chunks = conn.execute(
            select(
                document_chunks.c.classification,
                document_chunks.c.allowed_roles,
                document_chunks.c.allowed_user_ids,
                document_chunks.c.acl_version,
            ).where(document_chunks.c.document_id == document_id)
        ).all()
    assert doc.acl_version == 2
    assert set(doc.allowed_user_ids) == {member, seeded.admin_id}
    assert all(
        (c.classification, c.allowed_roles, set(c.allowed_user_ids), c.acl_version)
        == ("confidential", ["manager"], {member, seeded.admin_id}, 2)
        for c in chunks
    )
    assert _policy_version(owner_engine, seeded.tenant_id) == before + 1


def test_managers_manage_only_their_own_uploads_within_clearance(
    owner_engine: Engine, database_urls: DatabaseUrls, seeded: SeededTenant
) -> None:
    manager, email = create_user(owner_engine, seeded.tenant_id, role=Role.MANAGER)
    own = create_document(owner_engine, seeded.tenant_id, manager, allowed_user_ids=[manager])
    others = create_document(
        owner_engine, seeded.tenant_id, seeded.admin_id, allowed_roles=[Role.MANAGER]
    )
    body = {"classification": "internal", "allowed_roles": ["member"], "allowed_user_ids": []}
    with client_for(database_urls.app) as client:
        login(client, seeded.slug, email)
        h = csrf(client)
        assert (
            client.put(f"{API}/documents/{own}/permissions", json=body, headers=h).status_code
            == 200
        )
        assert (
            client.put(f"{API}/documents/{others}/permissions", json=body, headers=h).status_code
            == 403
        )
        raise_it = {**body, "classification": "restricted"}
        assert (
            client.put(f"{API}/documents/{own}/permissions", json=raise_it, headers=h).status_code
            == 403
        )


def test_delete_hides_document_removes_chunks_and_file(
    owner_engine: Engine, database_urls: DatabaseUrls, seeded: SeededTenant, tmp_path: Path
) -> None:
    with client_for(database_urls.app, upload_dir=str(tmp_path)) as client:
        login(client, seeded.slug, seeded.admin_email)
        document_id = upload(client, "a.txt", b"temporary", roles=["member"]).json()["id"]
        add_chunks(owner_engine, UUID(document_id), ["temporary"], [unit_vector(3)])
        before = _policy_version(owner_engine, seeded.tenant_id)
        assert (
            client.delete(f"{API}/documents/{document_id}", headers=csrf(client)).status_code == 204
        )
        assert client.get(f"{API}/documents/{document_id}").status_code == 404
    with owner_engine.connect() as conn:
        remaining = conn.execute(
            select(func.count())
            .select_from(document_chunks)
            .where(document_chunks.c.document_id == UUID(document_id))
        ).scalar_one()
    assert remaining == 0
    assert not (tmp_path / str(seeded.tenant_id) / document_id).exists()
    assert _policy_version(owner_engine, seeded.tenant_id) == before + 1


def test_a_permission_edit_based_on_a_stale_version_is_refused(
    owner_engine: Engine, database_urls: DatabaseUrls, seeded: SeededTenant
) -> None:
    document_id = create_document(
        owner_engine, seeded.tenant_id, seeded.admin_id, allowed_roles=[Role.MEMBER]
    )
    url = f"{API}/documents/{document_id}/permissions"
    with client_for(database_urls.app) as client:
        login(client, seeded.slug, seeded.admin_email)
        loaded = client.get(f"{API}/documents/{document_id}").json()["acl_version"]
        body = {"classification": "internal", "allowed_roles": ["manager"]}
        first = client.put(url, json={**body, "expected_acl_version": loaded}, headers=csrf(client))
        # A second editor who loaded the same version saves afterwards.
        second = client.put(
            url,
            json={
                "classification": "public",
                "allowed_roles": ["member"],
                "expected_acl_version": loaded,
            },
            headers=csrf(client),
        )
        # Without a version the change still applies (older clients, scripts).
        unversioned = client.put(url, json=body, headers=csrf(client))
    assert first.status_code == 200 and first.json()["acl_version"] == loaded + 1
    assert (second.status_code, second.json()["error"]["code"]) == (409, "conflict")
    assert "changed" in second.json()["error"]["message"]
    assert unversioned.status_code == 200
