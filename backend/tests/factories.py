import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from functools import cache
from uuid import UUID

from sqlalchemy import Engine, func, insert, select, update

from app.auth.passwords import hash_password
from app.auth.tokens import hash_token, new_session_token
from app.authz import Classification, Role
from app.db.schema import (
    EMBEDDING_DIM,
    document_chunks,
    documents,
    ingestion_jobs,
    sessions,
    tenants,
    users,
)

DEFAULT_PASSWORD = "correct-horse-battery-staple"


@cache
def _hash(password: str) -> str:
    return hash_password(password)


def create_tenant(
    owner_engine: Engine, *, slug: str | None = None, name: str = "Test Tenant"
) -> tuple[UUID, str]:
    slug = slug or f"t-{uuid.uuid4().hex[:12]}"
    with owner_engine.begin() as conn:
        tenant_id = conn.execute(
            insert(tenants).values(slug=slug, name=name).returning(tenants.c.id)
        ).scalar_one()
    return tenant_id, slug


def create_user(
    owner_engine: Engine,
    tenant_id: UUID,
    *,
    role: Role = Role.MEMBER,
    email: str | None = None,
    password: str = DEFAULT_PASSWORD,
    is_active: bool = True,
) -> tuple[UUID, str]:
    email = email or f"u-{uuid.uuid4().hex[:10]}@example.test"
    with owner_engine.begin() as conn:
        user_id = conn.execute(
            insert(users)
            .values(
                tenant_id=tenant_id,
                email=email,
                password_hash=_hash(password),
                role=role.value,
                is_active=is_active,
            )
            .returning(users.c.id)
        ).scalar_one()
    return user_id, email


def create_session(
    owner_engine: Engine,
    tenant_id: UUID,
    user_id: UUID,
    *,
    expires_in: timedelta = timedelta(hours=1),
    revoked: bool = False,
) -> str:
    token = new_session_token()
    with owner_engine.begin() as conn:
        conn.execute(
            insert(sessions).values(
                tenant_id=tenant_id,
                user_id=user_id,
                token_hash=hash_token(token),
                expires_at=func.now() + expires_in,
                revoked_at=func.now() if revoked else None,
            )
        )
    return token


@dataclass(frozen=True)
class SeededTenant:
    tenant_id: UUID
    slug: str
    admin_id: UUID
    admin_email: str


def create_tenant_with_admin(owner_engine: Engine, *, name: str = "Test Tenant") -> SeededTenant:
    tenant_id, slug = create_tenant(owner_engine, name=name)
    admin_id, admin_email = create_user(owner_engine, tenant_id, role=Role.ADMIN)
    return SeededTenant(tenant_id, slug, admin_id, admin_email)


def create_document(
    owner_engine: Engine,
    tenant_id: UUID,
    uploaded_by: UUID,
    *,
    classification: Classification = Classification.INTERNAL,
    allowed_roles: Iterable[Role] = (),
    allowed_user_ids: Iterable[UUID] = (),
    status: str = "ready",
    title: str = "Doc",
    acl_version: int = 1,
) -> UUID:
    document_id = uuid.uuid4()
    with owner_engine.begin() as conn:
        conn.execute(
            insert(documents).values(
                id=document_id,
                tenant_id=tenant_id,
                uploaded_by=uploaded_by,
                title=title,
                original_filename="doc.txt",
                mime_type="text/plain",
                size_bytes=1,
                sha256="0" * 64,
                storage_key=f"{tenant_id}/{document_id}",
                classification=classification.value,
                allowed_roles=sorted(r.value for r in allowed_roles),
                allowed_user_ids=sorted(allowed_user_ids, key=str),
                status=status,
                acl_version=acl_version,
            )
        )
    return document_id


def unit_vector(index: int) -> list[float]:
    vector = [0.0] * EMBEDDING_DIM
    vector[index % EMBEDDING_DIM] = 1.0
    return vector


def add_chunks(
    owner_engine: Engine,
    document_id: UUID,
    contents: Sequence[str],
    vectors: Sequence[Sequence[float]],
) -> list[UUID]:
    """Insert chunks that copy the document's current ACL, as the worker does."""
    ids: list[UUID] = []
    with owner_engine.begin() as conn:
        doc = conn.execute(
            select(
                documents.c.tenant_id,
                documents.c.classification,
                documents.c.allowed_roles,
                documents.c.allowed_user_ids,
                documents.c.acl_version,
            ).where(documents.c.id == document_id)
        ).one()
        for index, (content, vector) in enumerate(zip(contents, vectors, strict=True)):
            ids.append(
                conn.execute(
                    insert(document_chunks)
                    .values(
                        tenant_id=doc.tenant_id,
                        document_id=document_id,
                        chunk_index=index,
                        content=content,
                        embedding=list(vector),
                        classification=doc.classification,
                        allowed_roles=doc.allowed_roles,
                        allowed_user_ids=doc.allowed_user_ids,
                        acl_version=doc.acl_version,
                    )
                    .returning(document_chunks.c.id)
                ).scalar_one()
            )
    return ids


def create_job(
    owner_engine: Engine,
    tenant_id: UUID,
    document_id: UUID,
    *,
    status: str = "queued",
    run_after: timedelta = timedelta(0),
    locked_ago: timedelta | None = None,
) -> UUID:
    with owner_engine.begin() as conn:
        return conn.execute(  # type: ignore[no-any-return]
            insert(ingestion_jobs)
            .values(
                tenant_id=tenant_id,
                document_id=document_id,
                status=status,
                run_after=func.now() + run_after,
                locked_at=(func.now() - locked_ago) if locked_ago is not None else None,
            )
            .returning(ingestion_jobs.c.id)
        ).scalar_one()


def drain_jobs(owner_engine: Engine) -> None:
    """The job queue is global; tests that claim jobs start from an empty queue."""
    with owner_engine.begin() as conn:
        conn.execute(
            update(ingestion_jobs)
            .where(ingestion_jobs.c.status.in_(["queued", "running"]))
            .values(status="done")
        )
