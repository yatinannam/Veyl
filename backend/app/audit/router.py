from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.audit import service
from app.auth.deps import CurrentUser, EngineDep, TraceId
from app.text import SafeText

router = APIRouter(prefix="/audit-events", tags=["audit"])


class AuditEventOut(BaseModel):
    id: UUID
    occurred_at: datetime
    actor_user_id: UUID | None
    event_type: str
    decision: str
    query_ref: str | None
    query_text: str | None
    retrieved_chunk_ids: list[UUID]
    retrieved_document_ids: list[UUID]
    result_count: int | None
    trace_id: UUID | None
    model: str | None
    details: dict[str, Any]


class AuditPageOut(BaseModel):
    events: list[AuditEventOut]
    next_cursor: str | None


@router.get("")
def list_audit_events(
    ctx: CurrentUser,
    engine: EngineDep,
    trace_id: TraceId,
    user_id: UUID | None = None,
    document_id: UUID | None = None,
    event_type: Annotated[SafeText | None, Query(max_length=64)] = None,
    occurred_from: Annotated[datetime | None, Query(alias="from")] = None,
    occurred_to: Annotated[datetime | None, Query(alias="to")] = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPageOut:
    page = service.list_events(
        engine,
        ctx,
        service.AuditFilters(user_id, document_id, event_type, occurred_from, occurred_to),
        cursor=cursor,
        limit=limit,
        trace_id=trace_id,
    )
    return AuditPageOut(
        events=[AuditEventOut(**vars_of(e)) for e in page.events], next_cursor=page.next_cursor
    )


def vars_of(event: service.AuditEvent) -> dict[str, Any]:
    return {name: getattr(event, name) for name in event.__slots__}
