from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.audit import service as audit
from app.auth.passwords import hash_password
from app.authz import Action, AuthContext, Role, authorize
from app.db.schema import sessions, tenants, users
from app.db.tenancy import bump_policy_version, tenant_transaction
from app.errors import Conflict, Forbidden, NotFound


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: UUID
    email: str
    role: Role
    is_active: bool
    created_at: datetime


_COLUMNS = (users.c.id, users.c.email, users.c.role, users.c.is_active, users.c.created_at)


def _record(row: Any) -> UserRecord:
    return UserRecord(
        id=row.id,
        email=row.email,
        role=Role(row.role),
        is_active=row.is_active,
        created_at=row.created_at,
    )


def lock_tenant_membership(conn: Connection, tenant_id: UUID) -> None:
    """Serialise membership changes per tenant. FOR NO KEY UPDATE does not conflict with
    the KEY SHARE locks taken by foreign-key checks, so document and chunk inserts for the
    same tenant are never blocked by it."""
    conn.execute(
        select(tenants.c.id).where(tenants.c.id == tenant_id).with_for_update(key_share=True)
    )


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    id: UUID
    email: str
    role: Role


def directory(engine: Engine, ctx: AuthContext) -> list[DirectoryEntry]:
    """Active colleagues, so document owners can share with specific people."""
    authorize(ctx, Action.USER_DIRECTORY)
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        rows = conn.execute(
            select(users.c.id, users.c.email, users.c.role)
            .where(users.c.tenant_id == ctx.tenant_id, users.c.is_active)
            .order_by(users.c.email)
        ).all()
    return [DirectoryEntry(r.id, r.email, Role(r.role)) for r in rows]


def list_users(engine: Engine, ctx: AuthContext) -> list[UserRecord]:
    authorize(ctx, Action.USER_MANAGE)
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        rows = conn.execute(
            select(*_COLUMNS)
            .where(users.c.tenant_id == ctx.tenant_id)
            .order_by(users.c.created_at, users.c.email)
        ).all()
    return [_record(r) for r in rows]


def create_user(
    engine: Engine, ctx: AuthContext, *, email: str, password: str, role: Role
) -> UserRecord:
    authorize(ctx, Action.USER_MANAGE)
    password_hash = hash_password(password)
    try:
        with tenant_transaction(engine, ctx.tenant_id) as conn:
            row = conn.execute(
                insert(users)
                .values(
                    tenant_id=ctx.tenant_id,
                    email=email.strip().lower(),
                    password_hash=password_hash,
                    role=role.value,
                )
                .returning(*_COLUMNS)
            ).one()
            audit.record(
                conn,
                tenant_id=ctx.tenant_id,
                event_type="user.created",
                actor_user_id=ctx.user_id,
                details={"user_id": str(row.id), "role": row.role},
            )
    except IntegrityError as exc:
        raise Conflict("A user with this email already exists.") from exc
    return _record(row)


def update_user(
    engine: Engine,
    ctx: AuthContext,
    user_id: UUID,
    *,
    role: Role | None,
    is_active: bool | None,
) -> UserRecord:
    authorize(ctx, Action.USER_MANAGE)
    if user_id == ctx.user_id:
        raise Forbidden("Admins cannot change their own role or status.")
    values: dict[str, object] = {}
    if role is not None:
        values["role"] = role.value
    if is_active is not None:
        values["is_active"] = is_active

    with tenant_transaction(engine, ctx.tenant_id) as conn:
        lock_tenant_membership(conn, ctx.tenant_id)
        row = conn.execute(
            update(users)
            .where(users.c.id == user_id, users.c.tenant_id == ctx.tenant_id)
            .values(**values)
            .returning(*_COLUMNS)
        ).one_or_none()
        if row is None:
            raise NotFound()
        active_admins = conn.execute(
            select(func.count())
            .select_from(users)
            .where(
                users.c.tenant_id == ctx.tenant_id,
                users.c.role == Role.ADMIN.value,
                users.c.is_active,
            )
        ).scalar_one()
        if active_admins == 0:
            raise Conflict("A tenant must keep at least one active admin.")
        bump_policy_version(conn, ctx.tenant_id)
        if is_active is False:
            event_type = "user.deactivated"
        elif role is not None:
            event_type = "user.role_changed"
        else:
            event_type = "user.activated"
        audit.record(
            conn,
            tenant_id=ctx.tenant_id,
            event_type=event_type,
            actor_user_id=ctx.user_id,
            details={"user_id": str(user_id), "role": row.role, "is_active": row.is_active},
        )
        if is_active is False:
            conn.execute(
                update(sessions)
                .where(sessions.c.user_id == user_id, sessions.c.revoked_at.is_(None))
                .values(revoked_at=func.now())
            )
    return _record(row)
