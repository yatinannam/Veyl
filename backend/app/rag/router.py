from datetime import timedelta
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth.deps import CurrentUser, EngineDep, SettingsDep, TraceId, require_csrf
from app.config import Settings
from app.llm.client import LLMClient
from app.rag import service
from app.retrieval.embeddings import Embedder

router = APIRouter(tags=["query"])


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    conversation_id: UUID | None = None

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value


class CitationOut(BaseModel):
    label: str
    document_id: UUID
    document_title: str
    chunk_id: UUID
    snippet: str


class QueryResponse(BaseModel):
    conversation_id: UUID
    message_id: UUID
    answer: str
    citations: list[CitationOut]
    trace_id: UUID


def rag_config(settings: Settings) -> service.RagConfig:
    ttl = settings.retrieval_cache_ttl_seconds
    return service.RagConfig(
        top_k=settings.retrieval_top_k,
        min_score=settings.retrieval_min_score,
        cache_ttl=timedelta(seconds=ttl) if ttl else None,
        history_turns=settings.history_max_turns,
        audit_key=settings.audit_hmac_key.get_secret_value().encode(),
    )


@router.post("/query", dependencies=[Depends(require_csrf)])
def query(
    body: QueryRequest,
    ctx: CurrentUser,
    engine: EngineDep,
    settings: SettingsDep,
    trace_id: TraceId,
    request: Request,
) -> QueryResponse:
    answer = service.answer_question(
        engine,
        cast(Embedder, request.app.state.embedder),
        cast(LLMClient, request.app.state.llm),
        rag_config(settings),
        ctx,
        question=body.question,
        conversation_id=body.conversation_id,
        trace_id=trace_id,
    )
    return QueryResponse(
        conversation_id=answer.conversation_id,
        message_id=answer.message_id,
        answer=answer.answer,
        citations=[
            CitationOut(**{n: getattr(c, n) for n in c.__slots__}) for c in answer.citations
        ],
        trace_id=answer.trace_id,
    )
