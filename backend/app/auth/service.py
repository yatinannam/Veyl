from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import Connection, Engine, func, insert, select, text, update

from app.audit import service as audit
from app.auth.passwords import verify_password
from app.auth.ratelimit import LoginLimiters
from app.auth.tokens import hash_token, new_csrf_token, new_session_token
from app.authz import AuthContext, Role
from app.db.schema import sessions, tenants, users
from app.db.tenancy import tenant_transaction
from app.errors import InvalidCredentials, RateLimited


@dataclass(frozen=True, slots=True)
class Identity:
    user_id: UUID
    email: str
    role: Role
    tenant_id: UUID
    tenant_slug: str
    tenant_name: str


@dataclass(frozen=True, slots=True)
class LoginResult:
    session_token: str
    csrf_token: str
    identity: Identity


def _load_identity(conn: Connection, user_id: UUID) -> Identity:
    row = conn.execute(
        select(
            users.c.id,
            users.c.email,
            users.c.role,
            tenants.c.id.label("tenant_id"),
            tenants.c.slug,
            tenants.c.name,
        )
        .join_from(users, tenants, users.c.tenant_id == tenants.c.id)
        .where(users.c.id == user_id)
    ).one()
    return Identity(
        user_id=row.id,
        email=row.email,
        role=Role(row.role),
        tenant_id=row.tenant_id,
        tenant_slug=row.slug,
        tenant_name=row.name,
    )


def login(
    engine: Engine,
    limiters: LoginLimiters,
    *,
    tenant_slug: str,
    email: str,
    password: str,
    client_ip: str,
    session_ttl: timedelta,
    previous_token: str | None = None,
) -> LoginResult:
    slug = tenant_slug.strip().lower()
    email = email.strip().lower()
    account_key = f"account:{slug}:{email}"
    ip_key = f"ip:{client_ip}"
    # Every attempt is counted before the password is checked; a success gives it back.
    if not limiters.account.try_reserve(account_key):
        raise RateLimited()
    if not limiters.ip.try_reserve(ip_key):
        limiters.account.release(account_key)
        raise RateLimited()

    if not limiters.hashing.acquire(timeout=limiters.hash_wait_seconds):
        limiters.account.release(account_key)
        limiters.ip.release(ip_key)
        raise RateLimited()
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM auth_resolve_login(:slug, :email)"),
                {"slug": slug, "email": email},
            ).one_or_none()
        # Always verify (dummy hash for unknown users) so timing does not reveal accounts.
        password_ok = verify_password(row.password_hash if row else None, password)
    except BaseException:
        # The credentials were never checked (database down, for example): this attempt
        # must not count towards a lockout that would outlast the outage.
        limiters.account.release(account_key)
        limiters.ip.release(ip_key)
        raise
    finally:
        limiters.hashing.release()
    if row is None or not row.is_active or not password_ok:
        if row is not None:
            # The account exists, so the tenant is known and the attempt is auditable.
            with tenant_transaction(engine, row.tenant_id) as conn:
                audit.record(
                    conn,
                    tenant_id=row.tenant_id,
                    event_type="auth.login_failed",
                    actor_user_id=row.user_id,
                    decision="deny",
                    details={"reason": "inactive" if not row.is_active else "bad_password"},
                )
        raise InvalidCredentials()
    limiters.account.reset(account_key)
    limiters.ip.release(ip_key)
    # A browser that signs in again replaces its session; the old one must not live on.
    previous = resolve_session(engine, previous_token) if previous_token else None
    if previous is not None:
        logout(engine, previous)

    token = new_session_token()
    with tenant_transaction(engine, row.tenant_id) as conn:
        conn.execute(
            insert(sessions).values(
                tenant_id=row.tenant_id,
                user_id=row.user_id,
                token_hash=hash_token(token),
                # Database clock, not the API's: avoids skew between the two.
                expires_at=func.now() + session_ttl,
            )
        )
        identity = _load_identity(conn, row.user_id)
        audit.record(
            conn,
            tenant_id=row.tenant_id,
            event_type="auth.login_succeeded",
            actor_user_id=row.user_id,
        )
    return LoginResult(session_token=token, csrf_token=new_csrf_token(), identity=identity)


def resolve_session(engine: Engine, token: str) -> AuthContext | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM auth_resolve_session(:token_hash)"),
            {"token_hash": hash_token(token)},
        ).one_or_none()
    if row is None:
        return None
    return AuthContext(
        user_id=row.user_id,
        tenant_id=row.tenant_id,
        role=Role(row.role),
        session_id=row.session_id,
    )


def load_identity(engine: Engine, ctx: AuthContext) -> Identity:
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        return _load_identity(conn, ctx.user_id)


def logout(engine: Engine, ctx: AuthContext) -> None:
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        conn.execute(
            update(sessions).where(sessions.c.id == ctx.session_id).values(revoked_at=func.now())
        )
