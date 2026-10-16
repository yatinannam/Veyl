"""T2 (unauthorized retrieval within a tenant): the SQL predicates used inside queries and
the Python policy used for the re-check agree on every ACL in an exhaustive universe:
3 tenants (one unknown) x 3 users x 3 roles as callers, against
4 classifications x 8 role sets x 4 user sets per tenant."""

import uuid
from itertools import combinations
from uuid import UUID

import pytest
from sqlalchemy import Engine, insert, select, update

from app.authz import AuthContext, ChunkAcl, Classification, Role, can_read_chunk
from app.authz.sql import CHUNK_JOIN, chunk_read_filter, document_read_filter
from app.db.schema import document_chunks, documents
from tests.factories import add_chunks, create_document, create_tenant, create_user, unit_vector

pytestmark = pytest.mark.integration

ROLE_SETS = [frozenset(c) for n in range(4) for c in combinations(list(Role), n)]
USER_POOL = (UUID(int=101), UUID(int=102))
USER_SETS = [frozenset(c) for n in range(3) for c in combinations(USER_POOL, n)]
CALLER_IDS = (*USER_POOL, UUID(int=103))

Universe = tuple[list[UUID], dict[UUID, ChunkAcl], dict[UUID, ChunkAcl]]


@pytest.fixture(scope="module")
def universe(owner_engine: Engine) -> Universe:
    tenant_ids: list[UUID] = []
    chunk_acls: dict[UUID, ChunkAcl] = {}
    document_acls: dict[UUID, ChunkAcl] = {}
    for _ in range(2):
        tenant_id, _ = create_tenant(owner_engine)
        admin_id, _ = create_user(owner_engine, tenant_id, role=Role.ADMIN)
        tenant_ids.append(tenant_id)
        holder = create_document(owner_engine, tenant_id, admin_id)
        rows = []
        for classification in Classification:
            for roles in ROLE_SETS:
                for users in USER_SETS:
                    acl = ChunkAcl(tenant_id, roles, users, classification)
                    document_id = create_document(
                        owner_engine,
                        tenant_id,
                        admin_id,
                        classification=classification,
                        allowed_roles=roles,
                        allowed_user_ids=users,
                    )
                    document_acls[document_id] = acl
                    chunk_id = uuid.uuid4()
                    chunk_acls[chunk_id] = acl
                    rows.append(
                        {
                            "id": chunk_id,
                            "tenant_id": tenant_id,
                            "document_id": holder,
                            "chunk_index": len(rows),
                            "content": "x",
                            "embedding": unit_vector(len(rows)),
                            "classification": classification.value,
                            "allowed_roles": sorted(r.value for r in roles),
                            "allowed_user_ids": sorted(users, key=str),
                            "acl_version": 1,
                        }
                    )
        with owner_engine.begin() as conn:
            conn.execute(insert(document_chunks), rows)
    return tenant_ids, chunk_acls, document_acls


def _callers(tenant_ids: list[UUID]) -> list[AuthContext]:
    return [
        AuthContext(user_id=user, tenant_id=tenant, role=role, session_id=UUID(int=0))
        for tenant in (*tenant_ids, UUID(int=999))
        for user in CALLER_IDS
        for role in Role
    ]


def test_chunk_filter_matches_python_policy(owner_engine: Engine, universe: Universe) -> None:
    tenant_ids, chunk_acls, _ = universe
    with owner_engine.connect() as conn:
        for ctx in _callers(tenant_ids):
            from_sql = set(
                conn.execute(
                    select(document_chunks.c.id)
                    .select_from(CHUNK_JOIN)
                    .where(chunk_read_filter(ctx), document_chunks.c.id.in_(list(chunk_acls)))
                ).scalars()
            )
            from_python = {cid for cid, acl in chunk_acls.items() if can_read_chunk(ctx, acl)}
            assert from_sql == from_python, ctx


def test_document_filter_matches_python_policy(owner_engine: Engine, universe: Universe) -> None:
    tenant_ids, _, document_acls = universe
    with owner_engine.connect() as conn:
        for ctx in _callers(tenant_ids):
            from_sql = set(
                conn.execute(
                    select(documents.c.id).where(
                        document_read_filter(ctx), documents.c.id.in_(list(document_acls))
                    )
                ).scalars()
            )
            from_python = {d for d, acl in document_acls.items() if can_read_chunk(ctx, acl)}
            assert from_sql == from_python, ctx


def test_chunks_of_unready_deleted_or_stale_documents_are_invisible(
    owner_engine: Engine,
) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    admin_id, _ = create_user(owner_engine, tenant_id, role=Role.ADMIN)
    admin = AuthContext(
        user_id=admin_id, tenant_id=tenant_id, role=Role.ADMIN, session_id=UUID(int=0)
    )
    ready = create_document(owner_engine, tenant_id, admin_id)
    processing = create_document(owner_engine, tenant_id, admin_id, status="processing")
    deleted = create_document(owner_engine, tenant_id, admin_id)
    stale = create_document(owner_engine, tenant_id, admin_id)
    expected = add_chunks(owner_engine, ready, ["ok"], [unit_vector(1)])
    for document_id in (processing, deleted, stale):
        add_chunks(owner_engine, document_id, ["hidden"], [unit_vector(2)])
    with owner_engine.begin() as conn:
        conn.execute(
            update(documents)
            .where(documents.c.id == deleted)
            .values(deleted_at=documents.c.created_at)
        )
        conn.execute(
            update(documents)
            .where(documents.c.id == stale)
            .values(acl_version=documents.c.acl_version + 1)
        )
    with owner_engine.connect() as conn:
        visible = set(
            conn.execute(
                select(document_chunks.c.id)
                .select_from(CHUNK_JOIN)
                .where(chunk_read_filter(admin), document_chunks.c.tenant_id == tenant_id)
            ).scalars()
        )
    assert visible == set(expected)
