from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, delete, func, insert, select, update

from app.authz import Action, AuthContext, Classification, DocumentRef, Role, authorize, is_allowed
from app.authz.sql import document_read_filter
from app.db.schema import document_chunks, documents, ingestion_jobs, users
from app.db.tenancy import bump_policy_version, tenant_transaction
from app.documents.storage import LocalStorage
from app.documents.validation import ValidatedUpload
from app.errors import AppError, NotFound


class UnknownUsers(AppError):
    status_code = 422
    code = "unknown_users"
    default_message = "Some granted users do not exist in this workspace."


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    id: UUID
    title: str
    original_filename: str
    mime_type: str
    size_bytes: int
    classification: Classification
    allowed_roles: list[Role]
    allowed_user_ids: list[UUID]
    status: str
    error: str | None
    uploaded_by: UUID
    created_at: datetime
    updated_at: datetime


_COLUMNS = (
    documents.c.id,
    documents.c.title,
    documents.c.original_filename,
    documents.c.mime_type,
    documents.c.size_bytes,
    documents.c.classification,
    documents.c.allowed_roles,
    documents.c.allowed_user_ids,
    documents.c.status,
    documents.c.error,
    documents.c.uploaded_by,
    documents.c.created_at,
    documents.c.updated_at,
)


def _record(row: Any) -> DocumentRecord:
    return DocumentRecord(
        id=row.id,
        title=row.title,
        original_filename=row.original_filename,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        classification=Classification(row.classification),
        allowed_roles=[Role(r) for r in row.allowed_roles],
        allowed_user_ids=list(row.allowed_user_ids),
        status=row.status,
        error=row.error,
        uploaded_by=row.uploaded_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _ref(ctx: AuthContext, uploaded_by: UUID, classification: Classification) -> DocumentRef:
    return DocumentRef(
        tenant_id=ctx.tenant_id, uploaded_by=uploaded_by, classification=classification
    )


def can_manage(ctx: AuthContext, record: DocumentRecord) -> bool:
    return is_allowed(
        ctx, Action.DOCUMENT_MANAGE, _ref(ctx, record.uploaded_by, record.classification)
    )


def _acl(
    roles: Iterable[Role], user_ids: Iterable[UUID], uploaded_by: UUID
) -> tuple[list[str], list[UUID]]:
    """Normalise an ACL. The uploader always keeps access (spec section 4.3)."""
    return sorted({r.value for r in roles}), sorted({*user_ids, uploaded_by}, key=str)


def _require_tenant_users(conn: Connection, tenant_id: UUID, user_ids: list[UUID]) -> None:
    found = set(
        conn.execute(
            select(users.c.id).where(users.c.tenant_id == tenant_id, users.c.id.in_(user_ids))
        ).scalars()
    )
    if found != set(user_ids):
        raise UnknownUsers()


def upload_document(
    engine: Engine,
    storage: LocalStorage,
    ctx: AuthContext,
    *,
    upload: ValidatedUpload,
    data: bytes,
    title: str,
    classification: Classification,
    allowed_roles: Iterable[Role],
    allowed_user_ids: Iterable[UUID],
) -> DocumentRecord:
    authorize(ctx, Action.DOCUMENT_UPLOAD, _ref(ctx, ctx.user_id, classification))
    roles, user_ids = _acl(allowed_roles, allowed_user_ids, ctx.user_id)
    document_id = uuid4()
    key = LocalStorage.key_for(ctx.tenant_id, document_id)
    storage.save(key, data)
    try:
        with tenant_transaction(engine, ctx.tenant_id) as conn:
            _require_tenant_users(conn, ctx.tenant_id, user_ids)
            row = conn.execute(
                insert(documents)
                .values(
                    id=document_id,
                    tenant_id=ctx.tenant_id,
                    uploaded_by=ctx.user_id,
                    title=title,
                    original_filename=upload.original_filename,
                    mime_type=upload.mime_type,
                    size_bytes=upload.size_bytes,
                    sha256=upload.sha256,
                    storage_key=key,
                    classification=classification.value,
                    allowed_roles=roles,
                    allowed_user_ids=user_ids,
                )
                .returning(*_COLUMNS)
            ).one()
            conn.execute(
                insert(ingestion_jobs).values(tenant_id=ctx.tenant_id, document_id=document_id)
            )
    except BaseException:
        storage.delete(key)
        raise
    return _record(row)


def list_documents(engine: Engine, ctx: AuthContext) -> list[DocumentRecord]:
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        rows = conn.execute(
            select(*_COLUMNS)
            .where(document_read_filter(ctx))
            .order_by(documents.c.created_at.desc())
        ).all()
    return [_record(r) for r in rows]


def _readable_row(conn: Connection, ctx: AuthContext, document_id: UUID, *, lock: bool) -> Any:
    query = select(*_COLUMNS, documents.c.storage_key).where(
        documents.c.id == document_id, document_read_filter(ctx)
    )
    if lock:
        query = query.with_for_update(key_share=True)
    row = conn.execute(query).one_or_none()
    if row is None:
        raise NotFound()
    return row


def get_document(engine: Engine, ctx: AuthContext, document_id: UUID) -> DocumentRecord:
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        return _record(_readable_row(conn, ctx, document_id, lock=False))


def update_permissions(
    engine: Engine,
    ctx: AuthContext,
    document_id: UUID,
    *,
    classification: Classification,
    allowed_roles: Iterable[Role],
    allowed_user_ids: Iterable[UUID],
) -> DocumentRecord:
    """Spec section 5.4: document ACL, every chunk's ACL copy, acl_version, and the tenant
    policy_version change in one transaction."""
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        current = _readable_row(conn, ctx, document_id, lock=True)
        authorize(
            ctx,
            Action.DOCUMENT_MANAGE,
            _ref(ctx, current.uploaded_by, Classification(current.classification)),
        )
        authorize(ctx, Action.DOCUMENT_MANAGE, _ref(ctx, current.uploaded_by, classification))
        roles, user_ids = _acl(allowed_roles, allowed_user_ids, current.uploaded_by)
        _require_tenant_users(conn, ctx.tenant_id, user_ids)
        row = conn.execute(
            update(documents)
            .where(documents.c.id == document_id)
            .values(
                classification=classification.value,
                allowed_roles=roles,
                allowed_user_ids=user_ids,
                acl_version=documents.c.acl_version + 1,
                updated_at=func.now(),
            )
            .returning(*_COLUMNS, documents.c.acl_version)
        ).one()
        conn.execute(
            update(document_chunks)
            .where(
                document_chunks.c.document_id == document_id,
                document_chunks.c.tenant_id == ctx.tenant_id,
            )
            .values(
                classification=classification.value,
                allowed_roles=roles,
                allowed_user_ids=user_ids,
                acl_version=row.acl_version,
            )
        )
        bump_policy_version(conn, ctx.tenant_id)
    return _record(row)


def delete_document(
    engine: Engine, storage: LocalStorage, ctx: AuthContext, document_id: UUID
) -> None:
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        current = _readable_row(conn, ctx, document_id, lock=True)
        authorize(
            ctx,
            Action.DOCUMENT_MANAGE,
            _ref(ctx, current.uploaded_by, Classification(current.classification)),
        )
        conn.execute(
            update(documents)
            .where(documents.c.id == document_id)
            .values(deleted_at=func.now(), updated_at=func.now())
        )
        conn.execute(delete(document_chunks).where(document_chunks.c.document_id == document_id))
        bump_policy_version(conn, ctx.tenant_id)
    storage.delete(current.storage_key)
