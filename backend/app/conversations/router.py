from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from app.auth.deps import CurrentUser, EngineDep, require_csrf
from app.conversations import service

router = APIRouter(prefix="/conversations", tags=["conversations"])


class ConversationOut(BaseModel):
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class CitationOut(BaseModel):
    label: str
    document_id: UUID
    document_title: str
    chunk_id: UUID
    snippet: str


class MessageOut(BaseModel):
    id: UUID
    role: str
    content: str
    cited_document_ids: list[UUID]
    citations: list[CitationOut]
    trace_id: UUID
    created_at: datetime


def _message(stored: service.StoredMessage) -> MessageOut:
    fields = vars_of(stored)
    fields["citations"] = [CitationOut(**vars_of(c)) for c in stored.citations]
    return MessageOut(**fields)


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


@router.get("")
def list_conversations(ctx: CurrentUser, engine: EngineDep) -> list[ConversationOut]:
    return [ConversationOut(**vars_of(c)) for c in service.list_conversations(engine, ctx)]


@router.get("/{conversation_id}")
def get_conversation(
    conversation_id: UUID, ctx: CurrentUser, engine: EngineDep
) -> ConversationDetail:
    summary, stored = service.get_conversation(engine, ctx, conversation_id)
    return ConversationDetail(**vars_of(summary), messages=[_message(m) for m in stored])


@router.delete("/{conversation_id}", status_code=204, dependencies=[Depends(require_csrf)])
def delete_conversation(conversation_id: UUID, ctx: CurrentUser, engine: EngineDep) -> Response:
    service.delete_conversation(engine, ctx, conversation_id)
    return Response(status_code=204)


def vars_of(item: object) -> dict[str, object]:
    return {name: getattr(item, name) for name in item.__slots__}  # type: ignore[attr-defined]
