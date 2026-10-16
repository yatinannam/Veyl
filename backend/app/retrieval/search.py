"""Permission-filtered retrieval (spec sections 6.2-6.3).

One tenant transaction reads policy_version, the cache, and the vector search, so a cache
key can never be computed against one policy and used against another. The cache stores
chunk ids only; on a hit they are re-fetched through the same SQL filter. Every returned
row is then re-checked against every rule in Python, and any disagreement fails the
request before anything is returned."""

import hashlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.authz import AuthContext, ChunkAcl, Classification, Role, can_read_chunk
from app.authz.sql import CHUNK_JOIN, chunk_read_filter
from app.db.schema import document_chunks, documents, retrieval_cache, tenants
from app.db.tenancy import tenant_transaction
from app.errors import AppError
from app.retrieval.embeddings import Embedder

logger = logging.getLogger(__name__)


class AuthorizationMismatch(AppError):
    status_code = 500
    code = "authorization_mismatch"
    default_message = "An internal authorization check failed."


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: UUID
    document_id: UUID
    document_title: str
    chunk_index: int
    content: str
    score: float
    acl: ChunkAcl


_COLUMNS = (
    document_chunks.c.id,
    document_chunks.c.document_id,
    documents.c.title,
    document_chunks.c.chunk_index,
    document_chunks.c.content,
    document_chunks.c.tenant_id,
    document_chunks.c.classification,
    document_chunks.c.allowed_roles,
    document_chunks.c.allowed_user_ids,
    document_chunks.c.acl_version,
    documents.c.acl_version.label("document_acl_version"),
    documents.c.status.label("document_status"),
    documents.c.deleted_at.label("document_deleted_at"),
)


def normalise_query(query: str) -> str:
    return " ".join(query.lower().split())


def cache_key(
    ctx: AuthContext,
    policy_version: int,
    model_name: str,
    query: str,
    *,
    top_k: int,
    min_score: float,
) -> bytes:
    material = (
        f"{ctx.tenant_id}|{ctx.user_id}|{policy_version}|{model_name}|{top_k}|{min_score!r}|"
        f"{normalise_query(query)}"
    )
    return hashlib.sha256(material.encode("utf-8")).digest()


def _checked(ctx: AuthContext, row: Any, score: float) -> RetrievedChunk:
    """Second, independent check of every rule (spec section 4.3) on a row the SQL filter
    returned. Any disagreement means the filter is wrong: fail the request."""
    chunk = RetrievedChunk(
        chunk_id=row.id,
        document_id=row.document_id,
        document_title=row.title,
        chunk_index=row.chunk_index,
        content=row.content,
        score=score,
        acl=ChunkAcl(
            tenant_id=row.tenant_id,
            allowed_roles=frozenset(Role(r) for r in row.allowed_roles),
            allowed_user_ids=frozenset(row.allowed_user_ids),
            classification=Classification(row.classification),
        ),
    )
    document_live = (
        row.document_status == "ready"
        and row.document_deleted_at is None
        and row.acl_version == row.document_acl_version
    )
    if not (document_live and can_read_chunk(ctx, chunk.acl)):
        logger.error(
            "authorization mismatch: SQL filter returned chunk %s for user %s",
            chunk.chunk_id,
            ctx.user_id,
        )
        raise AuthorizationMismatch()
    return chunk


def fetch_readable_chunks(
    conn: Connection, ctx: AuthContext, chunk_ids: Iterable[UUID]
) -> list[RetrievedChunk]:
    """The subset of `chunk_ids` the caller may read right now (for cache hits, history,
    and conversation views). Order follows the input; scores are 0."""
    wanted = list(dict.fromkeys(chunk_ids))
    if not wanted:
        return []
    rows = conn.execute(
        select(*_COLUMNS)
        .select_from(CHUNK_JOIN)
        .where(chunk_read_filter(ctx), document_chunks.c.id.in_(wanted))
    ).all()
    found = {row.id: _checked(ctx, row, 0.0) for row in rows}
    return [found[i] for i in wanted if i in found]


def _cached(
    conn: Connection, ctx: AuthContext, key: bytes, policy_version: int
) -> list[tuple[UUID, float]] | None:
    row = conn.execute(
        select(retrieval_cache.c.chunk_ids, retrieval_cache.c.scores).where(
            retrieval_cache.c.cache_key == key,
            retrieval_cache.c.user_id == ctx.user_id,
            retrieval_cache.c.policy_version == policy_version,
            retrieval_cache.c.expires_at > func.now(),
        )
    ).one_or_none()
    if row is None:
        return None
    return list(zip(row.chunk_ids, row.scores, strict=True))


def _from_cache(
    conn: Connection, ctx: AuthContext, entries: list[tuple[UUID, float]]
) -> list[RetrievedChunk]:
    scores = dict(entries)
    readable = fetch_readable_chunks(conn, ctx, scores)
    return [
        RetrievedChunk(
            chunk_id=c.chunk_id,
            document_id=c.document_id,
            document_title=c.document_title,
            chunk_index=c.chunk_index,
            content=c.content,
            score=scores[c.chunk_id],
            acl=c.acl,
        )
        for c in readable
    ]


def _vector_search(
    conn: Connection, ctx: AuthContext, vector: list[float], top_k: int
) -> list[RetrievedChunk]:
    distance = document_chunks.c.embedding.cosine_distance(vector)
    rows = conn.execute(
        select(*_COLUMNS, distance.label("distance"))
        .select_from(CHUNK_JOIN)
        .where(chunk_read_filter(ctx))
        .order_by(distance)
        .limit(top_k)
    ).all()
    return [_checked(ctx, row, 1.0 - float(row.distance)) for row in rows]


def _store(
    conn: Connection,
    ctx: AuthContext,
    key: bytes,
    policy_version: int,
    results: list[RetrievedChunk],
    ttl: timedelta,
) -> None:
    conn.execute(delete(retrieval_cache).where(retrieval_cache.c.expires_at < func.now()))
    statement = pg_insert(retrieval_cache).values(
        cache_key=key,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        policy_version=policy_version,
        chunk_ids=[r.chunk_id for r in results],
        scores=[r.score for r in results],
        expires_at=func.now() + ttl,
    )
    conn.execute(
        statement.on_conflict_do_update(
            index_elements=[retrieval_cache.c.cache_key],
            set_={
                "policy_version": statement.excluded.policy_version,
                "chunk_ids": statement.excluded.chunk_ids,
                "scores": statement.excluded.scores,
                "created_at": func.now(),
                "expires_at": statement.excluded.expires_at,
            },
        )
    )


def retrieve(
    engine: Engine,
    embedder: Embedder,
    ctx: AuthContext,
    query: str,
    *,
    top_k: int,
    min_score: float = 0.0,
    cache_ttl: timedelta | None = None,
) -> list[RetrievedChunk]:
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        # RLS filters after the HNSW scan; iterative scans keep top-k recall for small tenants.
        conn.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        policy_version = int(
            conn.execute(
                select(tenants.c.policy_version).where(tenants.c.id == ctx.tenant_id)
            ).scalar_one()
        )
        key = cache_key(
            ctx, policy_version, embedder.model_name, query, top_k=top_k, min_score=min_score
        )
        entries = _cached(conn, ctx, key, policy_version) if cache_ttl else None
        if entries is not None:
            results = _from_cache(conn, ctx, entries)
        else:
            vector = embedder.embed_query(query)
            results = [r for r in _vector_search(conn, ctx, vector, top_k) if r.score >= min_score]
            if cache_ttl:
                _store(conn, ctx, key, policy_version, results, cache_ttl)
    return sorted(results, key=lambda r: r.score, reverse=True)
