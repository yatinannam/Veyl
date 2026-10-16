from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, cast, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import Text

from app.authz import AuthContext, Classification, Role
from app.db.schema import audit_events
from app.documents import service as documents_service
from app.documents.storage import LocalStorage
from app.documents.validation import validate_upload
from app.ingestion.pipeline import Ingestor
from tests.api import API, client_for, csrf, login
from tests.conftest import DatabaseUrls
from tests.factories import create_tenant_with_admin, create_user, drain_jobs
from tests.fakes import FakeEmbedder, encrypt_pdf, make_pdf

pytestmark = pytest.mark.integration


def _events(
    owner_engine: Engine, tenant_id: UUID
) -> list[tuple[str, str, UUID | None, dict[str, object]]]:
    with owner_engine.connect() as conn:
        rows = conn.execute(
            select(
                audit_events.c.event_type,
                audit_events.c.decision,
                audit_events.c.actor_user_id,
                audit_events.c.details,
            )
            .where(audit_events.c.tenant_id == tenant_id)
            .order_by(audit_events.c.occurred_at)
        ).all()
    return [(r.event_type, r.decision, r.actor_user_id, dict(r.details)) for r in rows]


def _types(owner_engine: Engine, tenant_id: UUID) -> list[str]:
    return [e[0] for e in _events(owner_engine, tenant_id)]


def test_logins_are_audited(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    t = create_tenant_with_admin(owner_engine)
    with client_for(database_urls.app) as client:
        login(client, t.slug, t.admin_email, "wrong-password-123")
        login(client, t.slug, "nobody@example.test", "wrong-password-123")
        login(client, t.slug, t.admin_email)
    events = _events(owner_engine, t.tenant_id)
    assert [(e[0], e[1], e[2]) for e in events] == [
        ("auth.login_failed", "deny", t.admin_id),
        ("auth.login_succeeded", "allow", t.admin_id),
    ]


def test_user_management_is_audited(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    t = create_tenant_with_admin(owner_engine)
    with client_for(database_urls.app) as client:
        login(client, t.slug, t.admin_email)
        h = csrf(client)
        new_id = client.post(
            f"{API}/users",
            json={"email": "n@x.test", "password": "another-long-password", "role": "member"},
            headers=h,
        ).json()["id"]
        client.patch(f"{API}/users/{new_id}", json={"role": "manager"}, headers=h)
        client.patch(f"{API}/users/{new_id}", json={"is_active": False}, headers=h)
        client.patch(f"{API}/users/{new_id}", json={"is_active": True}, headers=h)
        client.patch(f"{API}/tenant", json={"audit_query_mode": "plaintext"}, headers=h)
    types = _types(owner_engine, t.tenant_id)
    assert types[1:] == [
        "user.created",
        "user.role_changed",
        "user.deactivated",
        "user.activated",
        "tenant.updated",
    ]


def test_document_lifecycle_is_audited_without_content(
    owner_engine: Engine, app_engine: Engine, tmp_path: Path
) -> None:
    drain_jobs(owner_engine)
    t = create_tenant_with_admin(owner_engine)
    admin = AuthContext(
        user_id=t.admin_id, tenant_id=t.tenant_id, role=Role.ADMIN, session_id=uuid4()
    )
    storage = LocalStorage(tmp_path)
    secret = b"CANARY-AUDIT-4471 confidential acquisition target"
    ok = documents_service.upload_document(
        app_engine,
        storage,
        admin,
        upload=validate_upload("a.txt", secret, max_bytes=10_000),
        data=secret,
        title="Target",
        classification=Classification.INTERNAL,
        allowed_roles=[Role.MEMBER],
        allowed_user_ids=[],
    )
    bad_pdf = encrypt_pdf(make_pdf(["x"]))
    bad = documents_service.upload_document(
        app_engine,
        storage,
        admin,
        upload=validate_upload("b.pdf", bad_pdf, max_bytes=10_000),
        data=bad_pdf,
        title="Locked",
        classification=Classification.INTERNAL,
        allowed_roles=[],
        allowed_user_ids=[],
    )
    ingestor = Ingestor(app_engine, storage, FakeEmbedder(), max_pdf_pages=10)
    ingestor.run_once()
    ingestor.run_once()
    documents_service.update_permissions(
        app_engine,
        admin,
        ok.id,
        classification=Classification.CONFIDENTIAL,
        allowed_roles=[],
        allowed_user_ids=[],
    )
    documents_service.delete_document(app_engine, storage, admin, ok.id)
    events = _events(owner_engine, t.tenant_id)
    by_type = {e[0]: e for e in events}
    assert set(by_type) == {
        "document.uploaded",
        "document.ingested",
        "document.ingest_failed",
        "document.acl_changed",
        "document.deleted",
    }
    assert by_type["document.ingest_failed"][1] == "error"
    assert by_type["document.ingest_failed"][3]["document_id"] == str(bad.id)
    assert by_type["document.acl_changed"][3]["classification"] == "confidential"
    with owner_engine.connect() as conn:
        leaked = conn.execute(
            select(audit_events.c.id).where(
                audit_events.c.tenant_id == t.tenant_id,
                cast(cast(audit_events.c.details, JSONB), Text).contains("CANARY-AUDIT-4471"),
            )
        ).all()
    assert leaked == []


def test_tenant_reports_its_audit_query_mode(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    t = create_tenant_with_admin(owner_engine)
    _, member_email = create_user(owner_engine, t.tenant_id)
    with client_for(database_urls.app) as admin, client_for(database_urls.app) as member:
        login(admin, t.slug, t.admin_email)
        login(member, t.slug, member_email)
        assert admin.get(f"{API}/tenant").json()["audit_query_mode"] == "hash"
        assert (
            member.patch(
                f"{API}/tenant", json={"audit_query_mode": "plaintext"}, headers=csrf(member)
            ).status_code
            == 403
        )
        assert admin.patch(f"{API}/tenant", json={}, headers=csrf(admin)).status_code == 422
