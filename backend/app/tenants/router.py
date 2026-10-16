from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update

from app.auth.deps import CurrentUser, EngineDep, require_csrf
from app.authz import Action, authorize
from app.db.schema import tenants
from app.db.tenancy import tenant_transaction

router = APIRouter(prefix="/tenant", tags=["tenant"])


class TenantOut(BaseModel):
    id: UUID
    slug: str
    name: str


class TenantUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)


_COLUMNS = (tenants.c.id, tenants.c.slug, tenants.c.name)


@router.get("")
def get_tenant(ctx: CurrentUser, engine: EngineDep) -> TenantOut:
    authorize(ctx, Action.TENANT_READ)
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        row = conn.execute(select(*_COLUMNS).where(tenants.c.id == ctx.tenant_id)).one()
    return TenantOut(id=row.id, slug=row.slug, name=row.name)


@router.patch("", dependencies=[Depends(require_csrf)])
def update_tenant(body: TenantUpdate, ctx: CurrentUser, engine: EngineDep) -> TenantOut:
    authorize(ctx, Action.TENANT_UPDATE)
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        row = conn.execute(
            update(tenants)
            .where(tenants.c.id == ctx.tenant_id)
            .values(name=body.name)
            .returning(*_COLUMNS)
        ).one()
    return TenantOut(id=row.id, slug=row.slug, name=row.name)
