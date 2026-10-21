"""Returns the public demo to its seeded state, for a nightly job. Visitors may have
changed roles (even demoted the admin), deactivated or added people, uploaded, deleted, or
re-shared documents, and asked questions. The database part runs as the owner role; seed
documents that are gone or were changed are uploaded again through the API, as the seed
does. Audit records are never touched: the reset adds one `demo.reset` event per company."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, delete, insert, select, update

from app.audit import service as audit
from app.auth.passwords import hash_password
from app.db.schema import conversations, documents, retrieval_cache, sessions, tenants, users
from app.demo.content import TENANTS, DemoDocument, DemoTenant
from app.demo.http import ApiSession, Transport
from app.demo.seed import upload_document


class DemoMissing(Exception):
    """A demo company does not exist; run the seed first."""


@dataclass
class ResetReport:
    people_restored: int = 0
    people_removed: int = 0
    documents_removed: int = 0
    documents_reuploaded: list[str] = field(default_factory=list)


def _restore_people(
    conn: Connection, tenant_id: UUID, tenant: DemoTenant, password: str
) -> dict[str, UUID]:
    """Seeded people get their role, active state, and the demo password back (and are
    created again if missing). Returns their ids by email."""
    ids: dict[str, UUID] = {}
    for person in (tenant.admin, *tenant.people):
        values = {"role": person.role, "is_active": True, "password_hash": hash_password(password)}
        user_id = conn.execute(
            update(users)
            .where(users.c.tenant_id == tenant_id, users.c.email == person.email)
            .values(**values)
            .returning(users.c.id)
        ).scalar_one_or_none()
        if user_id is None:
            user_id = conn.execute(
                insert(users)
                .values(tenant_id=tenant_id, email=person.email, **values)
                .returning(users.c.id)
            ).scalar_one()
        ids[person.email] = user_id
    return ids


def _is_pristine(row: Any, document: DemoDocument, ids: dict[str, UUID], admin_id: UUID) -> bool:
    shared = {ids[email] for email in document.shared_with} | {admin_id}
    return (
        row.status == "ready"
        and row.uploaded_by == admin_id
        and row.classification == document.classification
        and set(row.allowed_roles) == set(document.allowed_roles)
        and set(row.allowed_user_ids) == shared
    )


def _reset_tenant(
    conn: Connection, tenant: DemoTenant, password: str, report: ResetReport
) -> list[DemoDocument]:
    """Everything except uploads, in one transaction. Returns the seed documents to upload."""
    tenant_id = conn.execute(
        select(tenants.c.id).where(tenants.c.slug == tenant.slug)
    ).scalar_one_or_none()
    if tenant_id is None:
        raise DemoMissing(tenant.slug)
    conn.execute(
        update(tenants)
        .where(tenants.c.id == tenant_id)
        .values(
            name=tenant.name,
            audit_query_mode="hash",
            policy_version=tenants.c.policy_version + 1,
        )
    )
    ids = _restore_people(conn, tenant_id, tenant, password)
    admin_id = ids[tenant.admin.email]

    # Keep one untouched copy of each seed document; everything else goes, including
    # documents visitors uploaded or deleted (their chunks and jobs cascade).
    by_title = {d.title: d for d in tenant.documents}
    keep: dict[str, UUID] = {}
    live = conn.execute(
        select(documents)
        .where(documents.c.tenant_id == tenant_id, documents.c.deleted_at.is_(None))
        .order_by(documents.c.created_at)
    )
    for row in live:
        document = by_title.get(row.title)
        if document and row.title not in keep and _is_pristine(row, document, ids, admin_id):
            keep[row.title] = row.id
    removed = conn.execute(
        delete(documents).where(
            documents.c.tenant_id == tenant_id, documents.c.id.not_in(list(keep.values()))
        )
    )
    report.documents_removed += removed.rowcount

    # Every session ends, every conversation and cached retrieval goes, and the people
    # visitors added are removed (their documents are already gone).
    conn.execute(delete(sessions).where(sessions.c.tenant_id == tenant_id))
    conn.execute(delete(conversations).where(conversations.c.tenant_id == tenant_id))
    conn.execute(delete(retrieval_cache).where(retrieval_cache.c.tenant_id == tenant_id))
    added = conn.execute(
        delete(users).where(users.c.tenant_id == tenant_id, users.c.email.not_in(list(ids)))
    )
    report.people_removed += added.rowcount
    report.people_restored += len(ids)

    missing = [d for d in tenant.documents if d.title not in keep]
    audit.record(
        conn,
        tenant_id=tenant_id,
        event_type="demo.reset",
        details={
            "people_removed": added.rowcount,
            "documents_removed": removed.rowcount,
            "documents_reuploaded": [d.title for d in missing],
        },
    )
    return missing


def reset_demo(
    owner_engine: Engine,
    new_transport: Callable[[], Transport],
    password: str,
    demo: Sequence[DemoTenant] = TENANTS,
) -> ResetReport:
    report = ResetReport()
    to_upload: list[tuple[DemoTenant, list[DemoDocument]]] = []
    with owner_engine.begin() as conn:
        for tenant in demo:
            missing = _reset_tenant(conn, tenant, password, report)
            if missing:
                to_upload.append((tenant, missing))
    for tenant, missing in to_upload:
        session = ApiSession(new_transport())
        session.login(tenant.slug, tenant.admin.email, password)
        ids = {person["email"]: person["id"] for person in session.get("/users")}
        for document in missing:
            upload_document(session, document, ids)
            report.documents_reuploaded.append(document.title)
        session.logout()
    return report
