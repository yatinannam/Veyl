"""Brief tests 1 (tenant isolation), 2 (role restrictions), 6 (permission changes),
7 (cache isolation); T14 (fail closed); plus the Python re-check as a second layer."""

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, event, func, insert, select, update

from app.authz import AuthContext, Classification, Role
from app.db.schema import document_chunks, documents, retrieval_cache, tenants
from app.db.tenancy import tenant_transaction
from app.documents import service as documents_service
from app.retrieval import search as search_module
from app.retrieval.search import (
    AuthorizationMismatch,
    RetrievedChunk,
    cache_key,
    fetch_readable_chunks,
    retrieve,
)
from tests.conftest import DatabaseUrls
from tests.factories import add_chunks, create_document, create_tenant_with_admin, create_user
from tests.fakes import FakeEmbedder

pytestmark = pytest.mark.integration

TTL = timedelta(minutes=10)


def _ctx(user_id: UUID, tenant_id: UUID, role: Role) -> AuthContext:
    return AuthContext(user_id=user_id, tenant_id=tenant_id, role=role, session_id=uuid4())


def _doc(
    owner_engine: Engine,
    embedder: FakeEmbedder,
    tenant_id: UUID,
    uploader: UUID,
    text: str,
    **acl: object,
) -> tuple[UUID, list[UUID]]:
    document_id = create_document(owner_engine, tenant_id, uploader, title=text[:40], **acl)  # type: ignore[arg-type]
    return document_id, add_chunks(
        owner_engine, document_id, [text], embedder.embed_documents([text])
    )


def _ids(results: list[RetrievedChunk]) -> set[UUID]:
    return {r.chunk_id for r in results}


def _search(
    app_engine: Engine,
    embedder: FakeEmbedder,
    ctx: AuthContext,
    query: str,
    *,
    top_k: int = 10,
    cache: bool = False,
) -> list[RetrievedChunk]:
    return retrieve(app_engine, embedder, ctx, query, top_k=top_k, cache_ttl=TTL if cache else None)


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


def test_tenant_a_cannot_retrieve_tenant_b_documents(
    owner_engine: Engine, app_engine: Engine, embedder: FakeEmbedder
) -> None:
    a = create_tenant_with_admin(owner_engine)
    b = create_tenant_with_admin(owner_engine)
    _, b_chunks = _doc(
        owner_engine,
        embedder,
        b.tenant_id,
        b.admin_id,
        "Project Falcon merger terms",
        allowed_roles=list(Role),
    )
    _, a_chunks = _doc(
        owner_engine,
        embedder,
        a.tenant_id,
        a.admin_id,
        "Project Falcon cafeteria menu",
        allowed_roles=list(Role),
    )
    results = _search(
        app_engine,
        embedder,
        _ctx(a.admin_id, a.tenant_id, Role.ADMIN),
        "Project Falcon merger terms",
    )
    assert _ids(results) == set(a_chunks)
    assert not _ids(results) & set(b_chunks)


def test_member_cannot_retrieve_admin_only_chunks(
    owner_engine: Engine, app_engine: Engine, embedder: FakeEmbedder
) -> None:
    t = create_tenant_with_admin(owner_engine)
    member, _ = create_user(owner_engine, t.tenant_id, role=Role.MEMBER)
    _, secret = _doc(
        owner_engine,
        embedder,
        t.tenant_id,
        t.admin_id,
        "board compensation figures",
        classification=Classification.RESTRICTED,
        allowed_roles=[Role.ADMIN],
    )
    _, handbook = _doc(
        owner_engine,
        embedder,
        t.tenant_id,
        t.admin_id,
        "employee handbook compensation basics",
        allowed_roles=[Role.MEMBER],
    )
    results = _search(
        app_engine, embedder, _ctx(member, t.tenant_id, Role.MEMBER), "board compensation figures"
    )
    assert _ids(results) == set(handbook)
    admin_results = _search(
        app_engine,
        embedder,
        _ctx(t.admin_id, t.tenant_id, Role.ADMIN),
        "board compensation figures",
    )
    assert set(secret) <= _ids(admin_results)


def test_clearance_caps_explicit_grants(
    owner_engine: Engine, app_engine: Engine, embedder: FakeEmbedder
) -> None:
    t = create_tenant_with_admin(owner_engine)
    member, _ = create_user(owner_engine, t.tenant_id, role=Role.MEMBER)
    manager, _ = create_user(owner_engine, t.tenant_id, role=Role.MANAGER)
    _, confidential = _doc(
        owner_engine,
        embedder,
        t.tenant_id,
        t.admin_id,
        "salary review notes",
        classification=Classification.CONFIDENTIAL,
        allowed_user_ids=[member, manager],
    )
    assert (
        _search(app_engine, embedder, _ctx(member, t.tenant_id, Role.MEMBER), "salary review notes")
        == []
    )
    assert _ids(
        _search(
            app_engine, embedder, _ctx(manager, t.tenant_id, Role.MANAGER), "salary review notes"
        )
    ) == set(confidential)


def test_permission_change_applies_to_the_next_retrieval(
    owner_engine: Engine, app_engine: Engine, embedder: FakeEmbedder
) -> None:
    t = create_tenant_with_admin(owner_engine)
    member, _ = create_user(owner_engine, t.tenant_id, role=Role.MEMBER)
    admin = _ctx(t.admin_id, t.tenant_id, Role.ADMIN)
    member_ctx = _ctx(member, t.tenant_id, Role.MEMBER)
    document_id, chunks = _doc(
        owner_engine,
        embedder,
        t.tenant_id,
        t.admin_id,
        "roadmap milestones",
        allowed_roles=[Role.MEMBER],
    )
    assert _ids(_search(app_engine, embedder, member_ctx, "roadmap milestones")) == set(chunks)
    documents_service.update_permissions(
        app_engine,
        admin,
        document_id,
        classification=Classification.INTERNAL,
        allowed_roles=[Role.ADMIN],
        allowed_user_ids=[],
    )
    assert _search(app_engine, embedder, member_ctx, "roadmap milestones") == []
    documents_service.update_permissions(
        app_engine,
        admin,
        document_id,
        classification=Classification.INTERNAL,
        allowed_roles=[Role.MEMBER],
        allowed_user_ids=[],
    )
    assert _ids(_search(app_engine, embedder, member_ctx, "roadmap milestones")) == set(chunks)


def test_stale_chunks_are_never_returned(
    owner_engine: Engine, app_engine: Engine, embedder: FakeEmbedder
) -> None:
    t = create_tenant_with_admin(owner_engine)
    document_id, _ = _doc(owner_engine, embedder, t.tenant_id, t.admin_id, "pricing sheet")
    with owner_engine.begin() as conn:
        conn.execute(
            update(documents)
            .where(documents.c.id == document_id)
            .values(acl_version=documents.c.acl_version + 1)
        )
    assert (
        _search(app_engine, embedder, _ctx(t.admin_id, t.tenant_id, Role.ADMIN), "pricing sheet")
        == []
    )


def test_identical_query_is_served_from_cache(
    owner_engine: Engine, app_engine: Engine, embedder: FakeEmbedder
) -> None:
    t = create_tenant_with_admin(owner_engine)
    _, chunks = _doc(owner_engine, embedder, t.tenant_id, t.admin_id, "travel policy")
    ctx = _ctx(t.admin_id, t.tenant_id, Role.ADMIN)
    first = _search(app_engine, embedder, ctx, "travel policy", cache=True)
    calls = embedder.query_calls
    second = _search(app_engine, embedder, ctx, "  Travel   POLICY ", cache=True)
    assert embedder.query_calls == calls
    assert _ids(first) == _ids(second) == set(chunks)


def test_cached_results_do_not_leak_to_a_less_privileged_user(
    owner_engine: Engine, app_engine: Engine, embedder: FakeEmbedder
) -> None:
    t = create_tenant_with_admin(owner_engine)
    member, _ = create_user(owner_engine, t.tenant_id, role=Role.MEMBER)
    _, secret = _doc(
        owner_engine,
        embedder,
        t.tenant_id,
        t.admin_id,
        "acquisition shortlist",
        classification=Classification.RESTRICTED,
        allowed_roles=[Role.ADMIN],
    )
    assert _ids(
        _search(
            app_engine,
            embedder,
            _ctx(t.admin_id, t.tenant_id, Role.ADMIN),
            "acquisition shortlist",
            cache=True,
        )
    ) == set(secret)
    assert (
        _search(
            app_engine,
            embedder,
            _ctx(member, t.tenant_id, Role.MEMBER),
            "acquisition shortlist",
            cache=True,
        )
        == []
    )


def test_forged_cache_entry_is_filtered_on_hit(
    owner_engine: Engine, app_engine: Engine, embedder: FakeEmbedder
) -> None:
    t = create_tenant_with_admin(owner_engine)
    member, _ = create_user(owner_engine, t.tenant_id, role=Role.MEMBER)
    member_ctx = _ctx(member, t.tenant_id, Role.MEMBER)
    _, secret = _doc(
        owner_engine,
        embedder,
        t.tenant_id,
        t.admin_id,
        "layoff plan",
        classification=Classification.RESTRICTED,
        allowed_roles=[Role.ADMIN],
    )
    with owner_engine.begin() as conn:
        version = conn.execute(
            select(tenants.c.policy_version).where(tenants.c.id == t.tenant_id)
        ).scalar_one()
        conn.execute(
            insert(retrieval_cache).values(
                cache_key=cache_key(
                    member_ctx, version, embedder.model_name, "layoff plan", top_k=10, min_score=0.0
                ),
                tenant_id=t.tenant_id,
                user_id=member,
                policy_version=version,
                chunk_ids=secret,
                scores=[0.99] * len(secret),
                expires_at=func.now() + TTL,
            )
        )
    calls = embedder.query_calls
    assert _search(app_engine, embedder, member_ctx, "layoff plan", cache=True) == []
    assert embedder.query_calls == calls  # the forged row was the cache hit, and it was filtered


def test_permission_change_invalidates_cached_results(
    owner_engine: Engine, app_engine: Engine, embedder: FakeEmbedder
) -> None:
    t = create_tenant_with_admin(owner_engine)
    member, _ = create_user(owner_engine, t.tenant_id, role=Role.MEMBER)
    member_ctx = _ctx(member, t.tenant_id, Role.MEMBER)
    document_id, chunks = _doc(
        owner_engine,
        embedder,
        t.tenant_id,
        t.admin_id,
        "bonus schedule",
        allowed_roles=[Role.MEMBER],
    )
    assert _ids(_search(app_engine, embedder, member_ctx, "bonus schedule", cache=True)) == set(
        chunks
    )
    documents_service.update_permissions(
        app_engine,
        _ctx(t.admin_id, t.tenant_id, Role.ADMIN),
        document_id,
        classification=Classification.INTERNAL,
        allowed_roles=[],
        allowed_user_ids=[],
    )
    assert _search(app_engine, embedder, member_ctx, "bonus schedule", cache=True) == []


def test_python_recheck_fails_closed_if_the_sql_filter_is_wrong(
    owner_engine: Engine,
    app_engine: Engine,
    embedder: FakeEmbedder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = create_tenant_with_admin(owner_engine)
    member, _ = create_user(owner_engine, t.tenant_id, role=Role.MEMBER)
    _doc(
        owner_engine,
        embedder,
        t.tenant_id,
        t.admin_id,
        "audit findings",
        classification=Classification.RESTRICTED,
        allowed_roles=[Role.ADMIN],
    )
    monkeypatch.setattr(
        search_module, "chunk_read_filter", lambda ctx: document_chunks.c.tenant_id == ctx.tenant_id
    )
    with pytest.raises(AuthorizationMismatch):
        _search(app_engine, embedder, _ctx(member, t.tenant_id, Role.MEMBER), "audit findings")


def test_python_recheck_rejects_chunks_of_documents_that_are_not_ready(
    owner_engine: Engine,
    app_engine: Engine,
    embedder: FakeEmbedder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = create_tenant_with_admin(owner_engine)
    _doc(owner_engine, embedder, t.tenant_id, t.admin_id, "draft budget", status="processing")
    monkeypatch.setattr(
        search_module, "chunk_read_filter", lambda ctx: document_chunks.c.tenant_id == ctx.tenant_id
    )
    with pytest.raises(AuthorizationMismatch):
        _search(app_engine, embedder, _ctx(t.admin_id, t.tenant_id, Role.ADMIN), "draft budget")


def test_cache_key_depends_on_result_shape() -> None:
    ctx = _ctx(uuid4(), uuid4(), Role.MEMBER)
    base = cache_key(ctx, 1, "m", "q", top_k=6, min_score=0.5)
    assert base != cache_key(ctx, 1, "m", "q", top_k=3, min_score=0.5)
    assert base != cache_key(ctx, 1, "m", "q", top_k=6, min_score=0.0)
    assert base == cache_key(ctx, 1, "m", " Q ", top_k=6, min_score=0.5)


def test_fetch_readable_chunks_drops_what_the_caller_cannot_read(
    owner_engine: Engine, app_engine: Engine, embedder: FakeEmbedder
) -> None:
    t = create_tenant_with_admin(owner_engine)
    member, _ = create_user(owner_engine, t.tenant_id, role=Role.MEMBER)
    _, shared = _doc(
        owner_engine, embedder, t.tenant_id, t.admin_id, "lunch menu", allowed_roles=[Role.MEMBER]
    )
    _, secret = _doc(
        owner_engine,
        embedder,
        t.tenant_id,
        t.admin_id,
        "board minutes",
        classification=Classification.RESTRICTED,
        allowed_roles=[Role.ADMIN],
    )
    member_ctx = _ctx(member, t.tenant_id, Role.MEMBER)
    with tenant_transaction(app_engine, t.tenant_id) as conn:
        readable = fetch_readable_chunks(conn, member_ctx, [*shared, *secret])
    assert [c.chunk_id for c in readable] == shared


def test_embedding_failure_fails_closed_without_fallback(
    owner_engine: Engine, app_engine: Engine
) -> None:
    t = create_tenant_with_admin(owner_engine)
    _doc(owner_engine, FakeEmbedder(), t.tenant_id, t.admin_id, "anything at all")
    with pytest.raises(RuntimeError):
        _search(
            app_engine,
            FakeEmbedder(fail=True),
            _ctx(t.admin_id, t.tenant_id, Role.ADMIN),
            "anything at all",
            cache=True,
        )
    with owner_engine.connect() as conn:
        rows = conn.execute(
            select(func.count())
            .select_from(retrieval_cache)
            .where(retrieval_cache.c.tenant_id == t.tenant_id)
        ).scalar_one()
    assert rows == 0


def test_small_tenant_keeps_recall_next_to_a_large_tenant(
    owner_engine: Engine, app_engine: Engine, embedder: FakeEmbedder
) -> None:
    big = create_tenant_with_admin(owner_engine)
    small = create_tenant_with_admin(owner_engine)
    query = "quarterly logistics report"
    texts = [query] * 300
    document_id = create_document(owner_engine, big.tenant_id, big.admin_id)
    add_chunks(owner_engine, document_id, texts, embedder.embed_documents(texts))
    _, mine = _doc(
        owner_engine,
        embedder,
        small.tenant_id,
        small.admin_id,
        "quarterly logistics report for the small office",
    )
    results = _search(
        app_engine, embedder, _ctx(small.admin_id, small.tenant_id, Role.ADMIN), query, top_k=3
    )
    assert _ids(results) == set(mine)


def test_retrieval_enables_iterative_index_scans_before_searching(
    owner_engine: Engine, database_urls: DatabaseUrls, embedder: FakeEmbedder
) -> None:
    """For large tenants the planner uses the HNSW index, and ACL filters apply to its
    candidates afterwards. Iterative scans (pgvector >= 0.8) keep searching when early
    candidates are filtered out. HNSW is approximate, so recall under heavy filtering cannot
    be asserted deterministically; this asserts the setting is applied, in the retrieval
    transaction, before the vector query. Small tenants are searched exactly through the
    tenant btree index (see the test above)."""
    t = create_tenant_with_admin(owner_engine)
    _doc(owner_engine, embedder, t.tenant_id, t.admin_id, "expense report rules")
    engine = create_engine(database_urls.app)
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        _search(engine, embedder, _ctx(t.admin_id, t.tenant_id, Role.ADMIN), "expense report rules")
    finally:
        engine.dispose()
    iterative = next(
        i for i, s in enumerate(statements) if "hnsw.iterative_scan = relaxed_order" in s
    )
    vector_query = next(i for i, s in enumerate(statements) if "<=>" in s)
    assert iterative < vector_query


def test_results_are_ordered_by_score(
    owner_engine: Engine, app_engine: Engine, embedder: FakeEmbedder
) -> None:
    t = create_tenant_with_admin(owner_engine)
    _doc(owner_engine, embedder, t.tenant_id, t.admin_id, "vacation policy days")
    _doc(owner_engine, embedder, t.tenant_id, t.admin_id, "vacation")
    results = _search(
        app_engine, embedder, _ctx(t.admin_id, t.tenant_id, Role.ADMIN), "vacation policy days"
    )
    assert [r.score for r in results] == sorted((r.score for r in results), reverse=True)
    assert results[0].content == "vacation policy days"
