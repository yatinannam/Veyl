"""A user's own conversations. History is re-authorized whenever it is read: an answer
that cited something the user can no longer read is hidden, both in the view and in the
history sent to the model (spec section 6.2, T13)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, delete, select

from app.authz import AuthContext
from app.db.schema import conversations, messages
from app.db.tenancy import tenant_transaction
from app.errors import NotFound
from app.llm.prompt import HistoryTurn
from app.retrieval.search import fetch_readable_chunks

HIDDEN_ANSWER = "[Earlier answer hidden: a source it used is no longer available to you.]"


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class StoredMessage:
    id: UUID
    role: str
    content: str
    cited_document_ids: list[UUID]
    trace_id: UUID
    created_at: datetime


_SUMMARY = (
    conversations.c.id,
    conversations.c.title,
    conversations.c.created_at,
    conversations.c.updated_at,
)


def owned_conversation(conn: Connection, ctx: AuthContext, conversation_id: UUID) -> Any | None:
    return conn.execute(
        select(*_SUMMARY).where(
            conversations.c.id == conversation_id,
            conversations.c.tenant_id == ctx.tenant_id,
            conversations.c.user_id == ctx.user_id,
        )
    ).one_or_none()


def _visible(conn: Connection, ctx: AuthContext, rows: list[Any]) -> list[StoredMessage]:
    cited = [chunk for row in rows for chunk in row.cited_chunk_ids]
    readable = {c.chunk_id for c in fetch_readable_chunks(conn, ctx, cited)}
    visible: list[StoredMessage] = []
    for row in rows:
        allowed = set(row.cited_chunk_ids) <= readable
        visible.append(
            StoredMessage(
                id=row.id,
                role=row.role,
                content=row.content if allowed else HIDDEN_ANSWER,
                cited_document_ids=list(row.cited_document_ids) if allowed else [],
                trace_id=row.trace_id,
                created_at=row.created_at,
            )
        )
    return visible


def _messages(conn: Connection, conversation_id: UUID, limit: int | None = None) -> list[Any]:
    query = (
        select(messages)
        .where(messages.c.conversation_id == conversation_id)
        .order_by(messages.c.created_at.desc(), messages.c.id.desc())
    )
    if limit is not None:
        query = query.limit(limit)
    return list(reversed(conn.execute(query).all()))


def history_for_prompt(
    conn: Connection, ctx: AuthContext, conversation_id: UUID, max_turns: int
) -> list[HistoryTurn]:
    if max_turns <= 0:
        return []
    rows = _messages(conn, conversation_id, limit=2 * max_turns)
    return [HistoryTurn(m.role, m.content) for m in _visible(conn, ctx, rows)]


def list_conversations(engine: Engine, ctx: AuthContext) -> list[ConversationSummary]:
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        rows = conn.execute(
            select(*_SUMMARY)
            .where(
                conversations.c.tenant_id == ctx.tenant_id,
                conversations.c.user_id == ctx.user_id,
            )
            .order_by(conversations.c.updated_at.desc())
        ).all()
    return [ConversationSummary(r.id, r.title, r.created_at, r.updated_at) for r in rows]


def get_conversation(
    engine: Engine, ctx: AuthContext, conversation_id: UUID
) -> tuple[ConversationSummary, list[StoredMessage]]:
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        row = owned_conversation(conn, ctx, conversation_id)
        if row is None:
            raise NotFound()
        visible = _visible(conn, ctx, _messages(conn, conversation_id))
    return ConversationSummary(row.id, row.title, row.created_at, row.updated_at), visible


def delete_conversation(engine: Engine, ctx: AuthContext, conversation_id: UUID) -> None:
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        if owned_conversation(conn, ctx, conversation_id) is None:
            raise NotFound()
        conn.execute(delete(conversations).where(conversations.c.id == conversation_id))
