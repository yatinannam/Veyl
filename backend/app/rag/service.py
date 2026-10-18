"""The secure RAG pipeline (spec section 6.2): ownership check, re-authorized history,
permission-filtered retrieval (fail closed), prompt with untrusted-source framing, LLM call
skipped when nothing is authorized, server-side citations, persistence, and audit."""

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, func, insert, select, update

from app.audit import service as audit
from app.authz import Action, AuthContext, authorize
from app.conversations.service import history_for_prompt, owned_conversation
from app.db.schema import conversations, messages, tenants
from app.db.tenancy import tenant_transaction
from app.errors import AppError, NotFound, ServiceUnavailable
from app.llm.citations import make_snippet, resolve_citations
from app.llm.client import LLMClient
from app.llm.prompt import HistoryTurn, build_messages
from app.retrieval.embeddings import Embedder
from app.retrieval.search import AuthorizationMismatch, RetrievedChunk, retrieve

logger = logging.getLogger(__name__)

NO_SOURCES_ANSWER = "I could not find anything about that in the documents you can access."


class LLMUnavailable(AppError):
    status_code = 503
    code = "llm_unavailable"
    default_message = "The language model is unavailable. Try again later."


@dataclass(frozen=True, slots=True)
class RagConfig:
    top_k: int
    min_score: float
    cache_ttl: timedelta | None
    history_turns: int
    audit_key: bytes


@dataclass(frozen=True, slots=True)
class Citation:
    label: str
    document_id: UUID
    document_title: str
    chunk_id: UUID
    snippet: str
    classification: str


@dataclass(frozen=True, slots=True)
class Answer:
    conversation_id: UUID
    message_id: UUID
    answer: str
    citations: list[Citation]
    trace_id: UUID


@dataclass(frozen=True, slots=True)
class _QueryAudit:
    query_ref: str
    query_text: str | None


def _record_failure(
    engine: Engine,
    ctx: AuthContext,
    query: _QueryAudit,
    trace_id: UUID,
    *,
    event_type: str,
    stage: str,
    chunks: list[RetrievedChunk] | None = None,
) -> None:
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        audit.record(
            conn,
            tenant_id=ctx.tenant_id,
            event_type=event_type,
            actor_user_id=ctx.user_id,
            decision="error",
            query_ref=query.query_ref,
            query_text=query.query_text,
            chunk_ids=[c.chunk_id for c in chunks or []],
            document_ids=[c.document_id for c in chunks or []],
            result_count=len(chunks) if chunks is not None else None,
            trace_id=trace_id,
            details={"stage": stage},
        )


def answer_question(
    engine: Engine,
    embedder: Embedder,
    llm: LLMClient,
    config: RagConfig,
    ctx: AuthContext,
    *,
    question: str,
    conversation_id: UUID | None,
    trace_id: UUID,
) -> Answer:
    authorize(ctx, Action.QUERY)
    question = question.strip()

    # 1. Ownership and re-authorized history, before any retrieval.
    history: list[HistoryTurn] = []
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        mode = conn.execute(
            select(tenants.c.audit_query_mode).where(tenants.c.id == ctx.tenant_id)
        ).scalar_one()
        query = _QueryAudit(
            query_ref=audit.query_reference(config.audit_key, ctx.tenant_id, question),
            query_text=question if mode == "plaintext" else None,
        )
        owned: Any = None
        if conversation_id is not None:
            owned = owned_conversation(conn, ctx, conversation_id)
            if owned is None:
                audit.record(
                    conn,
                    tenant_id=ctx.tenant_id,
                    event_type="rag.denied",
                    actor_user_id=ctx.user_id,
                    decision="deny",
                    query_ref=query.query_ref,
                    query_text=query.query_text,
                    trace_id=trace_id,
                    details={
                        "reason": "conversation_not_found",
                        "conversation_id": str(conversation_id),
                    },
                )
            else:
                history = history_for_prompt(conn, ctx, conversation_id, config.history_turns)
    if conversation_id is not None and owned is None:
        raise NotFound()

    # 2. Permission-filtered retrieval. No fallback path.
    try:
        chunks = retrieve(
            engine,
            embedder,
            ctx,
            question,
            top_k=config.top_k,
            min_score=config.min_score,
            cache_ttl=config.cache_ttl,
        )
    except AuthorizationMismatch:
        _record_failure(
            engine, ctx, query, trace_id, event_type="authz.mismatch", stage="retrieval"
        )
        raise
    except AppError:
        raise
    except Exception as exc:
        logger.warning("retrieval failed (trace_id=%s): %s", trace_id, type(exc).__name__)
        _record_failure(engine, ctx, query, trace_id, event_type="rag.query", stage="retrieval")
        raise ServiceUnavailable() from exc

    # 3. Generate only from authorized chunks; skip the model when there are none.
    model_name: str | None = None
    response_id: str | None = None
    citations: list[Citation] = []
    if not chunks:
        text = NO_SOURCES_ANSWER
    else:
        prompt, sources = build_messages(question, chunks, history)
        try:
            completion = llm.complete(prompt)
        except Exception as exc:
            # Any failure, foreseen (LLMError) or not (a provider response that breaks the
            # client), fails closed with the same answer and an audit record.
            logger.warning(
                "LLM call failed (trace_id=%s): %s: %s", trace_id, type(exc).__name__, exc
            )
            _record_failure(
                engine, ctx, query, trace_id, event_type="rag.query", stage="llm", chunks=chunks
            )
            raise LLMUnavailable() from exc
        resolved = resolve_citations(completion.text, sources)
        text = resolved.text or NO_SOURCES_ANSWER
        model_name, response_id = completion.model, completion.response_id
        citations = [
            Citation(
                label=s.label,
                document_id=s.chunk.document_id,
                document_title=s.chunk.document_title,
                chunk_id=s.chunk.chunk_id,
                snippet=make_snippet(s.chunk.content),
                classification=s.chunk.acl.classification.value,
            )
            for s in resolved.cited
        ]

    gone = False
    # 4. Persist the exchange and audit it in one transaction. The conversation may have
    # been deleted while the model was answering: re-check ownership under a row lock.
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        if conversation_id is None:
            conversation_id = conn.execute(
                insert(conversations)
                .values(tenant_id=ctx.tenant_id, user_id=ctx.user_id, title=question[:80])
                .returning(conversations.c.id)
            ).scalar_one()
        elif owned_conversation(conn, ctx, conversation_id, lock=True) is None:
            audit.record(
                conn,
                tenant_id=ctx.tenant_id,
                event_type="rag.denied",
                actor_user_id=ctx.user_id,
                decision="deny",
                query_ref=query.query_ref,
                query_text=query.query_text,
                trace_id=trace_id,
                details={
                    "reason": "conversation_deleted",
                    "conversation_id": str(conversation_id),
                },
            )
            gone = True
        else:
            conn.execute(
                update(conversations)
                .where(conversations.c.id == conversation_id)
                .values(updated_at=func.now())
            )
        if not gone:
            conn.execute(
                insert(messages).values(
                    tenant_id=ctx.tenant_id,
                    conversation_id=conversation_id,
                    role="user",
                    content=question,
                    trace_id=trace_id,
                    # now() is fixed for the whole transaction; the wall clock keeps the user
                    # message strictly before the answer so history replays in order.
                    created_at=func.clock_timestamp(),
                )
            )
            message_id = conn.execute(
                insert(messages)
                .values(
                    tenant_id=ctx.tenant_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=text,
                    cited_chunk_ids=[c.chunk_id for c in citations],
                    # Everything the model saw, cited or not: the answer is hidden once any of it
                    # becomes unreadable.
                    source_chunk_ids=[c.chunk_id for c in chunks],
                    cited_document_ids=list(dict.fromkeys(c.document_id for c in citations)),
                    trace_id=trace_id,
                    created_at=func.clock_timestamp(),
                )
                .returning(messages.c.id)
            ).scalar_one()
            audit.record(
                conn,
                tenant_id=ctx.tenant_id,
                event_type="rag.query",
                actor_user_id=ctx.user_id,
                query_ref=query.query_ref,
                query_text=query.query_text,
                chunk_ids=[c.chunk_id for c in chunks],
                document_ids=[c.document_id for c in chunks],
                result_count=len(chunks),
                trace_id=trace_id,
                model=model_name,
                details={
                    "cited_chunk_ids": [str(c.chunk_id) for c in citations],
                    "response_id": response_id,
                    "conversation_id": str(conversation_id),
                },
            )
    if gone:
        raise NotFound()
    return Answer(conversation_id, message_id, text, citations, trace_id)
