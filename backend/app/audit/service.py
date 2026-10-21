"""Append-only audit trail (spec section 8). Rows hold identifiers and decisions, never
document text, answers, or passwords; query text only when the tenant opts in."""

import base64
import binascii
import hashlib
import hmac
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, and_, any_, insert, literal, or_, select, tuple_
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.authz import Action, AuthContext, authorize
from app.db.schema import audit_events
from app.db.tenancy import tenant_transaction
from app.errors import AppError

EVENT_TYPES = frozenset(
    {
        "auth.login_succeeded",
        "auth.login_failed",
        "rag.query",
        "rag.denied",
        "authz.mismatch",
        "document.uploaded",
        "document.acl_changed",
        "document.deleted",
        "document.ingested",
        "document.ingest_failed",
        "user.created",
        "user.role_changed",
        "user.activated",
        "user.deactivated",
        "tenant.updated",
        "audit.viewed",
        "demo.reset",
    }
)


class InvalidCursor(AppError):
    status_code = 422
    code = "invalid_cursor"
    default_message = "The pagination cursor is invalid."


@dataclass(frozen=True, slots=True)
class AuditEvent:
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


@dataclass(frozen=True, slots=True)
class AuditFilters:
    user_id: UUID | None = None
    document_id: UUID | None = None
    event_type: str | None = None
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None


@dataclass(frozen=True, slots=True)
class AuditPage:
    events: list[AuditEvent]
    next_cursor: str | None


def query_reference(key: bytes, tenant_id: UUID, query: str) -> str:
    """Lets admins correlate identical questions without storing them."""
    normalised = " ".join(query.lower().split())
    return hmac.new(key, f"{tenant_id}|{normalised}".encode(), hashlib.sha256).hexdigest()


def record(
    conn: Connection,
    *,
    tenant_id: UUID,
    event_type: str,
    actor_user_id: UUID | None = None,
    decision: str = "allow",
    query_ref: str | None = None,
    query_text: str | None = None,
    chunk_ids: Iterable[UUID] = (),
    document_ids: Iterable[UUID] = (),
    result_count: int | None = None,
    trace_id: UUID | None = None,
    model: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown audit event type: {event_type}")
    conn.execute(
        insert(audit_events).values(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            event_type=event_type,
            decision=decision,
            query_ref=query_ref,
            query_text=query_text,
            retrieved_chunk_ids=list(dict.fromkeys(chunk_ids)),
            retrieved_document_ids=list(dict.fromkeys(document_ids)),
            result_count=result_count,
            trace_id=trace_id,
            model=model,
            details=details or {},
        )
    )


def _encode_cursor(occurred_at: datetime, event_id: UUID) -> str:
    raw = f"{occurred_at.isoformat()}|{event_id}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        occurred_at, event_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(occurred_at), UUID(event_id)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidCursor() from exc


def _event(row: Any) -> AuditEvent:
    return AuditEvent(
        id=row.id,
        occurred_at=row.occurred_at,
        actor_user_id=row.actor_user_id,
        event_type=row.event_type,
        decision=row.decision,
        query_ref=row.query_ref,
        query_text=row.query_text,
        retrieved_chunk_ids=list(row.retrieved_chunk_ids),
        retrieved_document_ids=list(row.retrieved_document_ids),
        result_count=row.result_count,
        trace_id=row.trace_id,
        model=row.model,
        details=dict(row.details),
    )


def list_events(
    engine: Engine,
    ctx: AuthContext,
    filters: AuditFilters,
    *,
    cursor: str | None,
    limit: int,
    trace_id: UUID | None,
) -> AuditPage:
    authorize(ctx, Action.AUDIT_READ)
    conditions = [audit_events.c.tenant_id == ctx.tenant_id]
    if filters.user_id:
        conditions.append(audit_events.c.actor_user_id == filters.user_id)
    if filters.document_id:
        conditions.append(
            or_(
                literal(filters.document_id, PG_UUID(as_uuid=True))
                == any_(audit_events.c.retrieved_document_ids),
                audit_events.c.details["document_id"].astext == str(filters.document_id),
            )
        )
    if filters.event_type:
        conditions.append(audit_events.c.event_type == filters.event_type)
    if filters.occurred_from:
        conditions.append(audit_events.c.occurred_at >= filters.occurred_from)
    if filters.occurred_to:
        conditions.append(audit_events.c.occurred_at <= filters.occurred_to)
    if cursor:
        at, event_id = _decode_cursor(cursor)
        conditions.append(
            tuple_(audit_events.c.occurred_at, audit_events.c.id) < tuple_(at, event_id)
        )

    with tenant_transaction(engine, ctx.tenant_id) as conn:
        rows = conn.execute(
            select(audit_events)
            .where(and_(*conditions))
            .order_by(audit_events.c.occurred_at.desc(), audit_events.c.id.desc())
            .limit(limit + 1)
        ).all()
        record(
            conn,
            tenant_id=ctx.tenant_id,
            event_type="audit.viewed",
            actor_user_id=ctx.user_id,
            trace_id=trace_id,
            details={
                "filters": {
                    "user_id": str(filters.user_id) if filters.user_id else None,
                    "document_id": str(filters.document_id) if filters.document_id else None,
                    "event_type": filters.event_type,
                }
            },
        )
    events = [_event(r) for r in rows[:limit]]
    more = len(rows) > limit
    return AuditPage(
        events=events,
        next_cursor=_encode_cursor(events[-1].occurred_at, events[-1].id) if more else None,
    )
