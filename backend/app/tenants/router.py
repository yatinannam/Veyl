from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update

from app.audit import service as audit
from app.auth.deps import CurrentUser, EngineDep, require_csrf
from app.authz import Action, authorize
from app.db.schema import tenants
from app.db.tenancy import tenant_transaction

router = APIRouter(prefix="/tenant", tags=["tenant"])

AuditQueryMode = Literal["hash", "plaintext"]


class TenantOut(BaseModel):
    id: UUID
    slug: str
    name: str
    audit_query_mode: AuditQueryMode


class TenantUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    audit_query_mode: AuditQueryMode | None = None

    @model_validator(mode="after")
    def _require_a_change(self) -> "TenantUpdate":
        if self.name is None and self.audit_query_mode is None:
            raise ValueError("Provide name and/or audit_query_mode.")
        return self


_COLUMNS = (tenants.c.id, tenants.c.slug, tenants.c.name, tenants.c.audit_query_mode)


@router.get("")
def get_tenant(ctx: CurrentUser, engine: EngineDep) -> TenantOut:
    authorize(ctx, Action.TENANT_READ)
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        row = conn.execute(select(*_COLUMNS).where(tenants.c.id == ctx.tenant_id)).one()
    return TenantOut.model_validate(row, from_attributes=True)


@router.patch("", dependencies=[Depends(require_csrf)])
def update_tenant(body: TenantUpdate, ctx: CurrentUser, engine: EngineDep) -> TenantOut:
    authorize(ctx, Action.TENANT_UPDATE)
    values = body.model_dump(exclude_none=True)
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        row = conn.execute(
            update(tenants)
            .where(tenants.c.id == ctx.tenant_id)
            .values(**values)
            .returning(*_COLUMNS)
        ).one()
        audit.record(
            conn,
            tenant_id=ctx.tenant_id,
            event_type="tenant.updated",
            actor_user_id=ctx.user_id,
            details=values,
        )
    return TenantOut.model_validate(row, from_attributes=True)
