import uuid

import pytest
from sqlalchemy import Engine, insert, select, text
from sqlalchemy.exc import IntegrityError

from app.authz import Role
from app.db.schema import document_chunks, documents
from app.db.tenancy import tenant_transaction
from app.users.service import lock_tenant_membership
from tests.factories import create_document, create_tenant, create_user, unit_vector

pytestmark = pytest.mark.integration


def _document_values(
    tenant_id: uuid.UUID, user_id: uuid.UUID, **overrides: object
) -> dict[str, object]:
    document_id = uuid.uuid4()
    values: dict[str, object] = {
        "id": document_id,
        "tenant_id": tenant_id,
        "uploaded_by": user_id,
        "title": "T",
        "original_filename": "t.txt",
        "mime_type": "text/plain",
        "size_bytes": 1,
        "sha256": "0" * 64,
        "storage_key": f"{tenant_id}/{document_id}",
        "classification": "internal",
    }
    values.update(overrides)
    return values


def test_pgvector_supports_iterative_index_scans(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        version = conn.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one()
    assert tuple(int(part) for part in version.split(".")[:2]) >= (0, 8)


def test_unknown_roles_are_rejected(owner_engine: Engine) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    user_id, _ = create_user(owner_engine, tenant_id, role=Role.ADMIN)
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        conn.execute(
            insert(documents).values(
                **_document_values(tenant_id, user_id, allowed_roles=["superuser"])
            )
        )


def test_chunk_cannot_reference_a_document_in_another_tenant(owner_engine: Engine) -> None:
    tenant_a, _ = create_tenant(owner_engine)
    tenant_b, _ = create_tenant(owner_engine)
    user_a, _ = create_user(owner_engine, tenant_a, role=Role.ADMIN)
    document_a = create_document(owner_engine, tenant_a, user_a)
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        conn.execute(
            insert(document_chunks).values(
                tenant_id=tenant_b,
                document_id=document_a,
                chunk_index=0,
                content="x",
                embedding=unit_vector(0),
                classification="internal",
                allowed_roles=[],
                allowed_user_ids=[],
                acl_version=1,
            )
        )


def test_document_uploader_must_belong_to_the_same_tenant(owner_engine: Engine) -> None:
    tenant_a, _ = create_tenant(owner_engine)
    tenant_b, _ = create_tenant(owner_engine)
    user_b, _ = create_user(owner_engine, tenant_b, role=Role.ADMIN)
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        conn.execute(insert(documents).values(**_document_values(tenant_a, user_b)))


def test_membership_lock_does_not_block_document_inserts(
    owner_engine: Engine, app_engine: Engine
) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    user_id, _ = create_user(owner_engine, tenant_id, role=Role.ADMIN)
    with tenant_transaction(app_engine, tenant_id) as locked:
        lock_tenant_membership(locked, tenant_id)
        with app_engine.connect() as other, other.begin():
            other.execute(text("SET LOCAL lock_timeout = '2s'"))
            other.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)}
            )
            other.execute(insert(documents).values(**_document_values(tenant_id, user_id)))
    with owner_engine.connect() as conn:
        count = conn.execute(select(documents.c.id).where(documents.c.tenant_id == tenant_id)).all()
    assert len(count) == 1
