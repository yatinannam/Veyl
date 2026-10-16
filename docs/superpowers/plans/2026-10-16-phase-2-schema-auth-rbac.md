# Phase 2 — Schema, RLS, Authentication, RBAC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Users of a tenant can log in with a server-side session, every request resolves an `AuthContext` from that session only, all tenant data sits behind Postgres row-level security, and a central `authz` policy decides every permission — proven by unit, integration, and security tests.

**Architecture:** Tables `tenants`, `users`, `sessions` (hand-written SQL migrations, SQLAlchemy Core table definitions) with RLS policies keyed on a transaction-local `app.tenant_id`. Two `SECURITY DEFINER` functions are the only cross-tenant reads (login lookup, session lookup). A pure `app.authz` package holds roles, clearance, actions, and the chunk read rule. FastAPI routers for auth, tenant, and users call services that open tenant-scoped transactions explicitly.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 Core + psycopg 3, Alembic, argon2-cffi, Hypothesis, pytest + testcontainers.

**Spec:** `docs/superpowers/specs/2026-10-15-veyl-design.md` (§4 authorization model, §5.1 roles/RLS, §5.2 ER, §7 API, §9 threats T1, T4, T5, T10, T15, T16)

## Global Constraints

- The spec is binding; deviations are recorded in `docs/decisions.md`.
- **No emojis anywhere** (enforced by `tests/unit/test_no_emojis.py`).
- **$0, no card**; no new paid services.
- **Every commit updates `README.md` and `docs/devlog.md`**, and is pushed to `origin main`.
- Tenant and user identity come **only** from the session cookie resolved server-side. Request models use `ConfigDict(extra="forbid")` and never contain `tenant_id`.
- Resources outside the caller's tenant return **404**, never 403.
- Deny by default: unknown actions, missing resources, missing context are all denied.
- `veyl_app` stays least-privilege: column-level grants only for what the API needs; no INSERT on `tenants`, no DELETE anywhere in this phase.
- Every `SECURITY DEFINER` function: `SET search_path = public, pg_temp`, `REVOKE ALL ... FROM PUBLIC`, explicit `GRANT EXECUTE ... TO veyl_app`.
- Security tests live in `backend/tests/security/`, module docstring names the threat IDs (T1, T4, ...).
- Test module basenames are unique across `tests/` subdirectories.
- Backend checks before every commit: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy` (run in `backend/`).

## Review Focus

1. **Email case and whitespace** (" Alice@Acme.test " at login; "BOB@x.test" vs "bob@x.test" at creation) — expect the same account at login and 409 on duplicate creation. Tests: Task 6 `test_login_normalises_email_and_slug`, Task 7 `test_create_user_rejects_case_insensitive_duplicate`.
2. **Non-ASCII CSRF header or cookie values** — `hmac.compare_digest` raises `TypeError` on non-ASCII `str`; expect 403, never 500. Test: Task 6 `test_non_ascii_csrf_header_is_rejected_not_crashed`.
3. **Session expiry computed from two clocks** — expect expiry derived from the database clock (`now() + interval`) so app/DB skew cannot shorten or extend sessions. Test: Task 6 `test_session_expiry_uses_database_clock`.
4. **Two admins demoting each other concurrently** — expect at least one active admin to remain (tenant row lock + invariant check). Test: Task 7 `test_concurrent_mutual_demotion_keeps_one_admin`.
5. **Tenant slug case at login** ("ACME" vs "acme") — expect the same tenant. Test: Task 6 `test_login_normalises_email_and_slug`.

## Spec refinements (record in `docs/decisions.md` in Task 8)

- RLS predicates call `app_current_tenant()` = `NULLIF(current_setting('app.tenant_id', true), '')::uuid`. After a transaction-local setting has been used once, Postgres returns `''` (not NULL) for it; a bare `''::uuid` cast would raise instead of returning zero rows.
- Each RLS table has two policies: `*_tenant_isolation` **TO veyl_app** and `*_owner_all` **TO veyl_owner**. `SECURITY DEFINER` functions run as `veyl_owner` and must read across tenants; FORCE RLS would otherwise hide all rows from them.
- `POST /auth/login` does not require the CSRF header (no CSRF cookie exists yet). It accepts only `application/json`, the API sends no CORS headers, and cookies are `SameSite=Lax`, so cross-site login forgery cannot submit it.
- Tenants are provisioned by an operator CLI using the owner role (`python -m app.cli provision-tenant`), not by any API.
- Auth audit events (`auth.login_*`) are written in Phase 4 when the audit table exists.
- SQLAlchemy **Core** (not ORM); migrations are hand-written SQL.

## File map

| Path | Responsibility |
|---|---|
| `backend/app/errors.py` | `AppError` hierarchy, error envelope, exception handlers |
| `backend/app/config.py` | Settings (SecretStr DB URL, session + login limits) |
| `backend/app/main.py` | App factory: handlers, trace-id middleware, routers, limiters |
| `backend/app/authz/{__init__,roles,context,policy}.py` | Roles, clearance, `AuthContext`, actions, `authorize`, `can_read_chunk` |
| `backend/app/auth/{passwords,tokens,ratelimit}.py` | argon2id, token generation/hashing, login rate limiter |
| `backend/app/auth/{service,deps,router}.py` | Login/logout/session resolution, FastAPI deps (auth, CSRF), `/auth/*` |
| `backend/app/db/schema.py` | Core `Table` definitions |
| `backend/app/db/tenancy.py` | `tenant_transaction()` |
| `backend/app/provisioning.py`, `backend/app/cli.py` | Operator tenant provisioning |
| `backend/app/tenants/router.py` | `/tenant` |
| `backend/app/users/{service,router}.py` | `/users` |
| `backend/migrations/versions/0001_tenants_users_sessions.py` | Tables, RLS, grants |
| `backend/migrations/versions/0002_auth_functions.py` | Definer functions |
| `backend/tests/factories.py`, `backend/tests/api.py` | Test data + HTTP helpers |
| `backend/tests/security/*.py` | Threat-mapped tests |

---

### Task 1: Error envelope, trace IDs, settings hardening

**Files:**
- Create: `backend/app/errors.py`, `backend/tests/unit/test_errors.py`
- Modify: `backend/app/config.py`, `backend/app/main.py`, `backend/tests/unit/test_config.py`, `README.md`, `docs/devlog.md`

**Interfaces:**
- Produces:
  - `app.errors`: `AppError(message: str | None = None)` with class attrs `status_code`, `code`, `default_message`; subclasses `Unauthenticated` (401 `unauthenticated`), `InvalidCredentials` (401 `invalid_credentials`), `Forbidden` (403 `forbidden`), `CsrfFailed` (403 `csrf_failed`), `NotFound` (404 `not_found`), `Conflict` (409 `conflict`), `RateLimited` (429 `rate_limited`), `ServiceUnavailable` (503 `service_unavailable`); `register_error_handlers(app: FastAPI) -> None`.
  - Error body: `{"error": {"code": str, "message": str, "trace_id": str | None, "details"?: list}}`; every response carries `X-Trace-Id`.
  - `sqlalchemy.exc.OperationalError` anywhere → 503 `service_unavailable` (fail closed).
  - `Settings.database_url: SecretStr`; `Settings.session_ttl_minutes: int = 720`; `Settings.login_max_failures: int = 5`; `Settings.login_window_seconds: int = 900`; property `Settings.cookie_secure -> bool` (True only when `environment == "production"`).

- [ ] **Step 1: Write failing tests**

Replace `backend/tests/unit/test_config.py`:
```python
import pytest
from pydantic import ValidationError

from app.config import Settings

URL = "postgresql+psycopg://u:secretpw@h:5432/d"


def test_settings_require_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings()


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("ENVIRONMENT", "test")
    s = Settings()
    assert s.database_url.get_secret_value() == URL
    assert s.environment == "test"


def test_settings_reject_unknown_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("ENVIRONMENT", "staging-ish")
    with pytest.raises(ValidationError):
        Settings()


def test_settings_never_reveal_database_password() -> None:
    s = Settings(database_url=URL)
    assert "secretpw" not in repr(s)
    assert "secretpw" not in str(s.model_dump())


@pytest.mark.parametrize(
    ("environment", "secure"),
    [("development", False), ("test", False), ("production", True)],
)
def test_cookies_are_secure_only_in_production(environment: str, secure: bool) -> None:
    assert Settings(database_url=URL, environment=environment).cookie_secure is secure


def test_session_ttl_has_sane_bounds() -> None:
    with pytest.raises(ValidationError):
        Settings(database_url=URL, session_ttl_minutes=1)
```

Create `backend/tests/unit/test_errors.py`:
```python
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.errors import Forbidden, RateLimited
from app.main import create_app

DB = "postgresql+psycopg://x:x@127.0.0.1:1/x"


class _Body(BaseModel):
    password: str
    age: int


def _app() -> FastAPI:
    app = create_app(Settings(database_url=DB))

    @app.get("/test/forbidden")
    def forbidden() -> None:
        raise Forbidden()

    @app.get("/test/limited")
    def limited() -> None:
        raise RateLimited()

    @app.post("/test/validate")
    def validate(body: _Body) -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/test/db-down")
    def db_down() -> None:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    @app.get("/test/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    return app


def test_app_errors_use_the_envelope_with_trace_id() -> None:
    with TestClient(_app()) as client:
        r = client.get("/test/forbidden")
    assert r.status_code == 403
    error = r.json()["error"]
    assert error["code"] == "forbidden"
    assert error["trace_id"] == r.headers["X-Trace-Id"]


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/test/limited", 429, "rate_limited"),
        ("/test/db-down", 503, "service_unavailable"),
        ("/does-not-exist", 404, "not_found"),
    ],
)
def test_status_and_code_mapping(path: str, status: int, code: str) -> None:
    with TestClient(_app()) as client:
        r = client.get(path)
    assert r.status_code == status
    assert r.json()["error"]["code"] == code


def test_validation_errors_never_echo_submitted_values() -> None:
    with TestClient(_app()) as client:
        r = client.post("/test/validate", json={"password": "hunter2-very-secret", "age": "nope-x"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    assert "hunter2-very-secret" not in r.text
    assert "nope-x" not in r.text


def test_unhandled_errors_return_generic_500() -> None:
    with TestClient(_app(), raise_server_exceptions=False) as client:
        r = client.get("/test/boom")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"
    assert "secret internal detail" not in r.text
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_config.py tests/unit/test_errors.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.errors'` and `AttributeError: 'str' object has no attribute 'get_secret_value'`.

- [ ] **Step 3: Implement**

`backend/app/config.py`:
```python
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Read from environment variables only (no .env loading):
    Docker Compose injects them, so a missing value fails loudly instead of defaulting."""

    model_config = SettingsConfigDict(extra="ignore")

    database_url: SecretStr
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "info"
    session_ttl_minutes: int = Field(default=720, ge=5, le=10080)
    login_max_failures: int = Field(default=5, ge=1)
    login_window_seconds: int = Field(default=900, ge=1)

    @property
    def cookie_secure(self) -> bool:
        return self.environment == "production"
```

`backend/app/errors.py`:
```python
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """An error that maps to a structured HTTP response."""

    status_code = 500
    code = "internal_error"
    default_message = "Internal server error."

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class Unauthenticated(AppError):
    status_code = 401
    code = "unauthenticated"
    default_message = "Authentication required."


class InvalidCredentials(AppError):
    status_code = 401
    code = "invalid_credentials"
    default_message = "Invalid workspace, email, or password."


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"
    default_message = "You do not have permission to perform this action."


class CsrfFailed(AppError):
    status_code = 403
    code = "csrf_failed"
    default_message = "CSRF token missing or invalid."


class NotFound(AppError):
    status_code = 404
    code = "not_found"
    default_message = "Resource not found."


class Conflict(AppError):
    status_code = 409
    code = "conflict"
    default_message = "Resource already exists."


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"
    default_message = "Too many attempts. Try again later."


class ServiceUnavailable(AppError):
    status_code = 503
    code = "service_unavailable"
    default_message = "A required service is unavailable."


_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}


def _trace_id(request: Request) -> str | None:
    return getattr(request.state, "trace_id", None)


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message, "trace_id": _trace_id(request)}
    if details is not None:
        body["details"] = details
    return JSONResponse({"error": body}, status_code=status_code)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return error_response(request, exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Never echo submitted values back: they may be passwords.
        details = [
            {"loc": list(err["loc"]), "msg": err["msg"], "type": err["type"]}
            for err in exc.errors()
        ]
        return error_response(
            request, 422, "validation_error", "Request validation failed.", details
        )

    @app.exception_handler(OperationalError)
    async def handle_db_unavailable(request: Request, exc: OperationalError) -> JSONResponse:
        logger.warning("database unavailable (trace_id=%s)", _trace_id(request))
        return error_response(
            request, 503, ServiceUnavailable.code, ServiceUnavailable.default_message
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        message = exc.detail if isinstance(exc.detail, str) else "HTTP error."
        return error_response(request, exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error (trace_id=%s)", _trace_id(request))
        return error_response(request, 500, "internal_error", "Internal server error.")
```

`backend/app/main.py`:
```python
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from app import health
from app.config import Settings
from app.db.engine import create_db_engine
from app.errors import register_error_handlers

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_db_engine(settings.database_url.get_secret_value())
        app.state.engine = engine
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="Veyl API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        redoc_url=None,
    )
    app.state.settings = settings
    register_error_handlers(app)

    @app.middleware("http")
    async def trace_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Server-generated only; a client-supplied trace id is never trusted.
        trace_id = str(uuid.uuid4())
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response

    app.include_router(health.router, prefix=API_PREFIX)
    return app
```

- [ ] **Step 4: Run tests, lint, types**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass (16 existing + new), clean.

- [ ] **Step 5: README + devlog, commit, push**

README: Phase 2 row -> "In progress (1/8 tasks)"; under the backend section add a short "Errors" paragraph: every error is `{"error": {"code", "message", "trace_id"}}` and responses carry `X-Trace-Id`. Devlog entry.

```bash
git add -A
git commit -m "feat(backend): structured error envelope, trace ids, secret-safe settings"
git push origin main
```

---

### Task 2: Authorization policy core (pure)

**Files:**
- Create: `backend/app/authz/__init__.py`, `backend/app/authz/roles.py`, `backend/app/authz/context.py`, `backend/app/authz/policy.py`, `backend/tests/unit/test_authz_policy.py`
- Modify: `backend/pyproject.toml` (add `hypothesis` dev dep), `README.md`, `docs/devlog.md`

**Interfaces:**
- Consumes: `app.errors.Forbidden`.
- Produces (all importable from `app.authz`):
  - `Role(StrEnum)`: `MEMBER="member"`, `MANAGER="manager"`, `ADMIN="admin"`.
  - `Classification(StrEnum)`: `PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, `RESTRICTED` (lowercase values).
  - `CLEARANCE: dict[Role, Classification]`; `within_clearance(role: Role, classification: Classification) -> bool`.
  - `AuthContext(user_id: UUID, tenant_id: UUID, role: Role, session_id: UUID)` — frozen dataclass.
  - `Action(StrEnum)`: `TENANT_READ="tenant.read"`, `TENANT_UPDATE="tenant.update"`, `USER_MANAGE="user.manage"`, `AUDIT_READ="audit.read"`, `QUERY="rag.query"`, `DOCUMENT_UPLOAD="document.upload"`, `DOCUMENT_MANAGE="document.manage"`.
  - `DocumentRef(tenant_id: UUID, uploaded_by: UUID | None, classification: Classification)`.
  - `ChunkAcl(tenant_id: UUID, allowed_roles: frozenset[Role], allowed_user_ids: frozenset[UUID], classification: Classification)`.
  - `is_allowed(ctx: AuthContext, action: Action, resource: DocumentRef | None = None) -> bool`; `authorize(...) -> None` (raises `Forbidden`); `permissions(role: Role) -> list[str]` (sorted UI hints); `can_read_chunk(ctx: AuthContext, acl: ChunkAcl) -> bool`.

- [ ] **Step 1: Add Hypothesis**

Run: `uv add --dev hypothesis`

- [ ] **Step 2: Write failing tests** — `backend/tests/unit/test_authz_policy.py`:

```python
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.authz import (
    Action,
    AuthContext,
    ChunkAcl,
    Classification,
    DocumentRef,
    Role,
    authorize,
    can_read_chunk,
    is_allowed,
    permissions,
    within_clearance,
)
from app.errors import Forbidden

T1, T2 = UUID(int=1), UUID(int=2)
U1, U2 = UUID(int=11), UUID(int=12)
SESSION = UUID(int=99)
C = Classification


def ctx(role: Role, tenant: UUID = T1, user: UUID = U1) -> AuthContext:
    return AuthContext(user_id=user, tenant_id=tenant, role=role, session_id=SESSION)


def doc(classification: Classification, uploaded_by: UUID | None = None, tenant: UUID = T1) -> DocumentRef:
    return DocumentRef(tenant_id=tenant, uploaded_by=uploaded_by, classification=classification)


def acl(
    classification: Classification,
    roles: set[Role] | None = None,
    users: set[UUID] | None = None,
    tenant: UUID = T1,
) -> ChunkAcl:
    return ChunkAcl(
        tenant_id=tenant,
        allowed_roles=frozenset(roles or set()),
        allowed_user_ids=frozenset(users or set()),
        classification=classification,
    )


@pytest.mark.parametrize(
    ("role", "classification", "expected"),
    [
        (Role.MEMBER, C.PUBLIC, True),
        (Role.MEMBER, C.INTERNAL, True),
        (Role.MEMBER, C.CONFIDENTIAL, False),
        (Role.MEMBER, C.RESTRICTED, False),
        (Role.MANAGER, C.CONFIDENTIAL, True),
        (Role.MANAGER, C.RESTRICTED, False),
        (Role.ADMIN, C.RESTRICTED, True),
    ],
)
def test_clearance(role: Role, classification: Classification, expected: bool) -> None:
    assert within_clearance(role, classification) is expected


@pytest.mark.parametrize(
    ("action", "member", "manager", "admin"),
    [
        (Action.TENANT_READ, True, True, True),
        (Action.QUERY, True, True, True),
        (Action.TENANT_UPDATE, False, False, True),
        (Action.USER_MANAGE, False, False, True),
        (Action.AUDIT_READ, False, False, True),
    ],
)
def test_action_matrix(action: Action, member: bool, manager: bool, admin: bool) -> None:
    assert is_allowed(ctx(Role.MEMBER), action) is member
    assert is_allowed(ctx(Role.MANAGER), action) is manager
    assert is_allowed(ctx(Role.ADMIN), action) is admin


def test_upload_requires_manager_and_clearance() -> None:
    assert not is_allowed(ctx(Role.MEMBER), Action.DOCUMENT_UPLOAD, doc(C.PUBLIC))
    assert is_allowed(ctx(Role.MANAGER), Action.DOCUMENT_UPLOAD, doc(C.CONFIDENTIAL))
    assert not is_allowed(ctx(Role.MANAGER), Action.DOCUMENT_UPLOAD, doc(C.RESTRICTED))
    assert is_allowed(ctx(Role.ADMIN), Action.DOCUMENT_UPLOAD, doc(C.RESTRICTED))


def test_manager_manages_only_own_documents_within_clearance() -> None:
    manager = ctx(Role.MANAGER, user=U1)
    assert is_allowed(manager, Action.DOCUMENT_MANAGE, doc(C.CONFIDENTIAL, uploaded_by=U1))
    assert not is_allowed(manager, Action.DOCUMENT_MANAGE, doc(C.INTERNAL, uploaded_by=U2))
    assert not is_allowed(manager, Action.DOCUMENT_MANAGE, doc(C.RESTRICTED, uploaded_by=U1))
    assert is_allowed(ctx(Role.ADMIN), Action.DOCUMENT_MANAGE, doc(C.RESTRICTED, uploaded_by=U2))


@pytest.mark.parametrize("role", list(Role))
def test_document_actions_without_a_resource_are_denied(role: Role) -> None:
    assert not is_allowed(ctx(role), Action.DOCUMENT_UPLOAD)
    assert not is_allowed(ctx(role), Action.DOCUMENT_MANAGE)


def test_document_actions_in_another_tenant_are_denied() -> None:
    assert not is_allowed(ctx(Role.ADMIN), Action.DOCUMENT_MANAGE, doc(C.PUBLIC, tenant=T2))


def test_authorize_raises_forbidden_when_denied() -> None:
    with pytest.raises(Forbidden):
        authorize(ctx(Role.MEMBER), Action.USER_MANAGE)


def test_authorize_returns_none_when_allowed() -> None:
    assert authorize(ctx(Role.ADMIN), Action.USER_MANAGE) is None


def test_permission_hints_per_role() -> None:
    assert permissions(Role.MEMBER) == ["rag.query", "tenant.read"]
    assert permissions(Role.MANAGER) == ["document.upload", "rag.query", "tenant.read"]
    assert permissions(Role.ADMIN) == [
        "audit.read",
        "document.upload",
        "rag.query",
        "tenant.read",
        "tenant.update",
        "user.manage",
    ]


@pytest.mark.parametrize("role", list(Role))
def test_permission_hints_agree_with_policy(role: Role) -> None:
    for action in (Action.TENANT_READ, Action.TENANT_UPDATE, Action.USER_MANAGE, Action.AUDIT_READ, Action.QUERY):
        assert (str(action) in permissions(role)) is is_allowed(ctx(role), action)


def test_member_reads_chunk_shared_with_members() -> None:
    assert can_read_chunk(ctx(Role.MEMBER), acl(C.INTERNAL, roles={Role.MEMBER}))


def test_member_cannot_read_admin_only_chunk() -> None:
    assert not can_read_chunk(ctx(Role.MEMBER), acl(C.RESTRICTED, roles={Role.ADMIN}))


def test_explicit_user_grant_is_still_bounded_by_clearance() -> None:
    granted = acl(C.CONFIDENTIAL, users={U1})
    assert not can_read_chunk(ctx(Role.MEMBER, user=U1), granted)
    assert can_read_chunk(ctx(Role.MANAGER, user=U1), granted)
    assert not can_read_chunk(ctx(Role.MANAGER, user=U2), granted)


def test_admin_reads_any_chunk_in_own_tenant() -> None:
    assert can_read_chunk(ctx(Role.ADMIN), acl(C.RESTRICTED))


def test_no_chunk_is_readable_across_tenants() -> None:
    assert not can_read_chunk(ctx(Role.ADMIN, tenant=T1), acl(C.PUBLIC, roles=set(Role), tenant=T2))


_ids = st.sampled_from([UUID(int=i) for i in range(3)])
_roles = st.sampled_from(list(Role))
_classes = st.sampled_from(list(Classification))
_contexts = st.builds(AuthContext, user_id=_ids, tenant_id=_ids, role=_roles, session_id=st.just(SESSION))
_acls = st.builds(
    ChunkAcl,
    tenant_id=_ids,
    allowed_roles=st.frozensets(_roles),
    allowed_user_ids=st.frozensets(_ids),
    classification=_classes,
)
_docs = st.builds(DocumentRef, tenant_id=_ids, uploaded_by=st.none() | _ids, classification=_classes)


@given(_contexts, _acls)
def test_readable_chunks_are_same_tenant_and_within_clearance(c: AuthContext, a: ChunkAcl) -> None:
    if can_read_chunk(c, a):
        assert a.tenant_id == c.tenant_id
        assert within_clearance(c.role, a.classification)


@given(_contexts, _acls)
def test_readable_chunks_have_a_grant(c: AuthContext, a: ChunkAcl) -> None:
    if can_read_chunk(c, a):
        assert c.role is Role.ADMIN or c.role in a.allowed_roles or c.user_id in a.allowed_user_ids


@given(_contexts, _docs, st.sampled_from([Action.DOCUMENT_UPLOAD, Action.DOCUMENT_MANAGE]))
def test_document_actions_never_cross_tenant_or_exceed_clearance(
    c: AuthContext, d: DocumentRef, action: Action
) -> None:
    if is_allowed(c, action, d):
        assert d.tenant_id == c.tenant_id
        assert within_clearance(c.role, d.classification)
        assert c.role is not Role.MEMBER
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/unit/test_authz_policy.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.authz'`.

- [ ] **Step 4: Implement**

`backend/app/authz/roles.py`:
```python
from enum import StrEnum


class Role(StrEnum):
    MEMBER = "member"
    MANAGER = "manager"
    ADMIN = "admin"


class Classification(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


_RANK = {
    Classification.PUBLIC: 0,
    Classification.INTERNAL: 1,
    Classification.CONFIDENTIAL: 2,
    Classification.RESTRICTED: 3,
}

CLEARANCE: dict[Role, Classification] = {
    Role.MEMBER: Classification.INTERNAL,
    Role.MANAGER: Classification.CONFIDENTIAL,
    Role.ADMIN: Classification.RESTRICTED,
}


def within_clearance(role: Role, classification: Classification) -> bool:
    """True if `role` may see material at `classification`."""
    return _RANK[classification] <= _RANK[CLEARANCE[role]]
```

`backend/app/authz/context.py`:
```python
from dataclasses import dataclass
from uuid import UUID

from app.authz.roles import Role


@dataclass(frozen=True, slots=True)
class AuthContext:
    """Who is calling. Built only from a server-side session lookup, never from
    client-supplied identifiers."""

    user_id: UUID
    tenant_id: UUID
    role: Role
    session_id: UUID
```

`backend/app/authz/policy.py`:
```python
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.authz.context import AuthContext
from app.authz.roles import Classification, Role, within_clearance
from app.errors import Forbidden


class Action(StrEnum):
    TENANT_READ = "tenant.read"
    TENANT_UPDATE = "tenant.update"
    USER_MANAGE = "user.manage"
    AUDIT_READ = "audit.read"
    QUERY = "rag.query"
    DOCUMENT_UPLOAD = "document.upload"
    DOCUMENT_MANAGE = "document.manage"


@dataclass(frozen=True, slots=True)
class DocumentRef:
    """What policy needs to know about a document (existing, or about to be uploaded)."""

    tenant_id: UUID
    uploaded_by: UUID | None
    classification: Classification


@dataclass(frozen=True, slots=True)
class ChunkAcl:
    tenant_id: UUID
    allowed_roles: frozenset[Role]
    allowed_user_ids: frozenset[UUID]
    classification: Classification


_EVERYONE = frozenset({Action.TENANT_READ, Action.QUERY})
_ADMIN_ONLY = frozenset({Action.TENANT_UPDATE, Action.USER_MANAGE, Action.AUDIT_READ})
_UPLOADERS = frozenset({Role.MANAGER, Role.ADMIN})


def is_allowed(ctx: AuthContext, action: Action, resource: DocumentRef | None = None) -> bool:
    if action in _EVERYONE:
        return True
    if action in _ADMIN_ONLY:
        return ctx.role is Role.ADMIN
    # Document actions: deny unless a same-tenant resource within clearance is given.
    if resource is None or resource.tenant_id != ctx.tenant_id:
        return False
    if not within_clearance(ctx.role, resource.classification):
        return False
    if action is Action.DOCUMENT_UPLOAD:
        return ctx.role in _UPLOADERS
    if action is Action.DOCUMENT_MANAGE:
        return ctx.role is Role.ADMIN or (
            ctx.role is Role.MANAGER and resource.uploaded_by == ctx.user_id
        )
    return False


def authorize(ctx: AuthContext, action: Action, resource: DocumentRef | None = None) -> None:
    if not is_allowed(ctx, action, resource):
        raise Forbidden()


def permissions(role: Role) -> list[str]:
    """Coarse capability hints for the UI. The API re-checks every request."""
    granted = set(_EVERYONE)
    if role is Role.ADMIN:
        granted |= _ADMIN_ONLY
    if role in _UPLOADERS:
        granted.add(Action.DOCUMENT_UPLOAD)
    return sorted(str(action) for action in granted)


def can_read_chunk(ctx: AuthContext, acl: ChunkAcl) -> bool:
    """The chunk read rule (spec §4.3), minus the document status/version checks
    that only exist in SQL. Phase 3 adds the SQL form and an equivalence test."""
    if acl.tenant_id != ctx.tenant_id:
        return False
    if not within_clearance(ctx.role, acl.classification):
        return False
    return (
        ctx.role is Role.ADMIN
        or ctx.role in acl.allowed_roles
        or ctx.user_id in acl.allowed_user_ids
    )
```

`backend/app/authz/__init__.py`:
```python
"""The single place that decides permissions. Other modules call into it."""

from app.authz.context import AuthContext
from app.authz.policy import (
    Action,
    ChunkAcl,
    DocumentRef,
    authorize,
    can_read_chunk,
    is_allowed,
    permissions,
)
from app.authz.roles import CLEARANCE, Classification, Role, within_clearance

__all__ = [
    "CLEARANCE",
    "Action",
    "AuthContext",
    "ChunkAcl",
    "Classification",
    "DocumentRef",
    "Role",
    "authorize",
    "can_read_chunk",
    "is_allowed",
    "permissions",
    "within_clearance",
]
```

- [ ] **Step 5: Run full suite + lint + types**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: PASS, clean.

- [ ] **Step 6: README (add "Authorization model" summary: roles, clearance table, chunk rule), devlog, commit, push**

```bash
git add -A
git commit -m "feat(authz): central policy with roles, clearance, actions, chunk read rule"
git push origin main
```

---

### Task 3: Security primitives — passwords, tokens, login rate limiter

**Files:**
- Create: `backend/app/auth/__init__.py` (empty), `backend/app/auth/passwords.py`, `backend/app/auth/tokens.py`, `backend/app/auth/ratelimit.py`, `backend/tests/unit/test_auth_primitives.py`
- Modify: `backend/pyproject.toml` (add `argon2-cffi`), `README.md`, `docs/devlog.md`

**Interfaces:**
- Produces:
  - `app.auth.passwords`: `MIN_PASSWORD_LENGTH = 12`, `MAX_PASSWORD_LENGTH = 1024`, `hash_password(password: str) -> str` (argon2id), `verify_password(password_hash: str | None, password: str) -> bool` (runs a dummy verify when hash is None; never raises).
  - `app.auth.tokens`: `new_session_token() -> str`, `new_csrf_token() -> str`, `hash_token(token: str) -> bytes` (32-byte SHA-256 digest).
  - `app.auth.ratelimit`: `LoginRateLimiter(*, max_failures: int, window_seconds: float, clock: Callable[[], float] = time.monotonic)` with `is_blocked(key) -> bool`, `record_failure(key) -> None`, `reset(key) -> None`; `LoginLimiters(account: LoginRateLimiter, ip: LoginRateLimiter)` with classmethod `create(*, max_failures: int, window_seconds: float) -> LoginLimiters` (ip limit = 4x).

- [ ] **Step 1: Add dependency**

Run: `uv add argon2-cffi`

- [ ] **Step 2: Write failing tests** — `backend/tests/unit/test_auth_primitives.py`:

```python
import hashlib

from app.auth.passwords import hash_password, verify_password
from app.auth.ratelimit import LoginLimiters, LoginRateLimiter
from app.auth.tokens import hash_token, new_csrf_token, new_session_token


def test_password_hash_is_argon2id_and_verifies() -> None:
    h = hash_password("correct horse battery")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "correct horse battery")
    assert not verify_password(h, "wrong password!!")


def test_verify_without_a_hash_is_false() -> None:
    assert verify_password(None, "anything at all") is False


def test_verify_with_a_malformed_hash_is_false() -> None:
    assert verify_password("not-a-real-hash", "anything at all") is False


def test_session_tokens_are_unique_and_long() -> None:
    tokens = {new_session_token() for _ in range(200)}
    assert len(tokens) == 200
    assert all(len(t) >= 43 for t in tokens)


def test_csrf_tokens_are_unique() -> None:
    assert new_csrf_token() != new_csrf_token()


def test_token_hash_is_a_sha256_digest() -> None:
    assert hash_token("abc") == hashlib.sha256(b"abc").digest()
    assert len(hash_token("abc")) == 32


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_limiter_blocks_after_max_failures_in_window() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(max_failures=3, window_seconds=60, clock=clock)
    for _ in range(2):
        limiter.record_failure("k")
    assert not limiter.is_blocked("k")
    limiter.record_failure("k")
    assert limiter.is_blocked("k")
    assert not limiter.is_blocked("other")


def test_limiter_forgets_failures_after_window() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(max_failures=1, window_seconds=60, clock=clock)
    limiter.record_failure("k")
    assert limiter.is_blocked("k")
    clock.now += 61
    assert not limiter.is_blocked("k")


def test_limiter_reset_clears_key() -> None:
    limiter = LoginRateLimiter(max_failures=1, window_seconds=60)
    limiter.record_failure("k")
    limiter.reset("k")
    assert not limiter.is_blocked("k")


def test_ip_limit_is_looser_than_account_limit() -> None:
    limiters = LoginLimiters.create(max_failures=2, window_seconds=60)
    for _ in range(2):
        limiters.account.record_failure("a")
        limiters.ip.record_failure("i")
    assert limiters.account.is_blocked("a")
    assert not limiters.ip.is_blocked("i")
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/unit/test_auth_primitives.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.auth'`.

- [ ] **Step 4: Implement**

`backend/app/auth/passwords.py`:
```python
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024  # caps hashing work per request

_hasher = PasswordHasher()  # argon2id, library defaults (RFC 9106 low-memory profile)
_DUMMY_HASH = _hasher.hash("veyl-timing-equaliser")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-shape verify: with no hash (unknown user) we still pay the hashing cost
    so response timing does not reveal which accounts exist."""
    try:
        matched = _hasher.verify(password_hash or _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return False
    return matched and password_hash is not None
```

`backend/app/auth/tokens.py`:
```python
import hashlib
import secrets


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> bytes:
    """Session tokens are stored only as this digest; a database leak yields no usable tokens."""
    return hashlib.sha256(token.encode("utf-8")).digest()
```

`backend/app/auth/ratelimit.py`:
```python
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

IP_LIMIT_MULTIPLIER = 4


class LoginRateLimiter:
    """Sliding-window failure counter, in-process (single API replica; spec §11)."""

    def __init__(
        self,
        *,
        max_failures: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_failures
        self._window = window_seconds
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str) -> deque[float]:
        cutoff = self._clock() - self._window
        entries = self._failures.setdefault(key, deque())
        while entries and entries[0] <= cutoff:
            entries.popleft()
        return entries

    def is_blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._prune(key)) >= self._max

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._prune(key).append(self._clock())

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


@dataclass(frozen=True, slots=True)
class LoginLimiters:
    account: LoginRateLimiter
    ip: LoginRateLimiter

    @classmethod
    def create(cls, *, max_failures: int, window_seconds: float) -> "LoginLimiters":
        return cls(
            account=LoginRateLimiter(max_failures=max_failures, window_seconds=window_seconds),
            ip=LoginRateLimiter(
                max_failures=max_failures * IP_LIMIT_MULTIPLIER, window_seconds=window_seconds
            ),
        )
```

- [ ] **Step 5: Run full suite + lint + types** — Expected: PASS, clean.

- [ ] **Step 6: README (note argon2id + hashed session tokens), devlog, commit, push**

```bash
git add -A
git commit -m "feat(auth): argon2id passwords, hashed session tokens, login rate limiter"
git push origin main
```

---

### Task 4: Schema migration 0001 with row-level security

**Files:**
- Create: `backend/migrations/versions/0001_tenants_users_sessions.py`, `backend/app/db/schema.py`, `backend/app/db/tenancy.py`, `backend/tests/factories.py`, `backend/tests/integration/test_schema.py`, `backend/tests/security/test_rls_isolation.py` (no `__init__.py` in `tests/security/`; basenames stay unique)
- Modify: `backend/tests/conftest.py` (add `owner_engine`, `app_engine` fixtures), `README.md`, `docs/devlog.md`

**Interfaces:**
- Consumes: `Role` (Task 2), `hash_password` (Task 3).
- Produces:
  - DB: type `user_role`; function `app_current_tenant() -> uuid`; tables `tenants(id, slug, name, policy_version, created_at)`, `users(id, tenant_id, email citext, password_hash, role, is_active, created_at)`, `sessions(id, tenant_id, user_id, token_hash bytea(32), created_at, expires_at, revoked_at)`.
  - Grants to `veyl_app`: `tenants` SELECT + UPDATE(name, policy_version); `users` SELECT, INSERT, UPDATE(role, is_active); `sessions` SELECT, INSERT, UPDATE(revoked_at).
  - `app.db.schema`: `metadata`, `tenants`, `users`, `sessions` (`sqlalchemy.Table`).
  - `app.db.tenancy.tenant_transaction(engine: Engine, tenant_id: UUID) -> ContextManager[Connection]` (raises `TypeError` unless `tenant_id` is a `UUID`).
  - Fixtures: `owner_engine`, `app_engine` (session-scoped `Engine`).
  - `tests.factories`: `DEFAULT_PASSWORD`, `create_tenant(owner_engine, *, slug=None, name="Test Tenant") -> tuple[UUID, str]`, `create_user(owner_engine, tenant_id, *, role=Role.MEMBER, email=None, password=DEFAULT_PASSWORD, is_active=True) -> tuple[UUID, str]`.

- [ ] **Step 1: Fixtures and factories**

Append to `backend/tests/conftest.py`:
```python
@pytest.fixture(scope="session")
def owner_engine(database_urls: DatabaseUrls) -> Iterator[Engine]:
    engine = create_engine(database_urls.owner)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app_engine(database_urls: DatabaseUrls) -> Iterator[Engine]:
    engine = create_engine(database_urls.app)
    yield engine
    engine.dispose()
```
(and add `from sqlalchemy import Engine` to the imports).

`backend/tests/factories.py`:
```python
import uuid
from functools import cache
from uuid import UUID

from sqlalchemy import Engine, insert

from app.auth.passwords import hash_password
from app.authz import Role
from app.db.schema import tenants, users

DEFAULT_PASSWORD = "correct-horse-battery-staple"


@cache
def _hash(password: str) -> str:
    return hash_password(password)


def create_tenant(
    owner_engine: Engine, *, slug: str | None = None, name: str = "Test Tenant"
) -> tuple[UUID, str]:
    slug = slug or f"t-{uuid.uuid4().hex[:12]}"
    with owner_engine.begin() as conn:
        tenant_id = conn.execute(
            insert(tenants).values(slug=slug, name=name).returning(tenants.c.id)
        ).scalar_one()
    return tenant_id, slug


def create_user(
    owner_engine: Engine,
    tenant_id: UUID,
    *,
    role: Role = Role.MEMBER,
    email: str | None = None,
    password: str = DEFAULT_PASSWORD,
    is_active: bool = True,
) -> tuple[UUID, str]:
    email = email or f"u-{uuid.uuid4().hex[:10]}@example.test"
    with owner_engine.begin() as conn:
        user_id = conn.execute(
            insert(users)
            .values(
                tenant_id=tenant_id,
                email=email,
                password_hash=_hash(password),
                role=role.value,
                is_active=is_active,
            )
            .returning(users.c.id)
        ).scalar_one()
    return user_id, email
```

- [ ] **Step 2: Write failing tests**

`backend/tests/integration/test_schema.py`:
```python
import pytest
from sqlalchemy import Engine, func, insert, inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.schema import metadata, sessions
from tests.factories import create_tenant, create_user

pytestmark = pytest.mark.integration


def test_schema_module_matches_migrated_database(owner_engine: Engine) -> None:
    inspector = inspect(owner_engine)
    for table in metadata.sorted_tables:
        db_columns = {c["name"] for c in inspector.get_columns(table.name)}
        assert db_columns == set(table.c.keys()), table.name


def test_every_table_has_forced_row_level_security(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' "
                "AND c.relname <> 'alembic_version'"
            )
        ).all()
    assert rows, "no tables found"
    assert [r.relname for r in rows if not (r.relrowsecurity and r.relforcerowsecurity)] == []


def test_session_cannot_reference_a_user_in_another_tenant(owner_engine: Engine) -> None:
    tenant_a, _ = create_tenant(owner_engine)
    tenant_b, _ = create_tenant(owner_engine)
    user_a, _ = create_user(owner_engine, tenant_a)
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        conn.execute(
            insert(sessions).values(
                tenant_id=tenant_b, user_id=user_a, token_hash=b"x" * 32, expires_at=func.now()
            )
        )
```

`backend/tests/security/test_rls_isolation.py`:
```python
"""T1 (cross-tenant leakage) and T5 (missing authorization context), database layer.

RLS must hide every row when no tenant is set, show only the current tenant's rows
when one is, and refuse writes into another tenant."""

import uuid

import pytest
from sqlalchemy import Engine, delete, func, insert, select, text, update
from sqlalchemy.exc import DataError, ProgrammingError

from app.db.schema import sessions, tenants, users
from app.db.tenancy import tenant_transaction
from tests.factories import create_tenant, create_user

pytestmark = pytest.mark.integration


def _count(conn, table) -> int:  # type: ignore[no-untyped-def]
    return conn.execute(select(func.count()).select_from(table)).scalar_one()


def test_no_tenant_context_sees_no_rows(owner_engine: Engine, app_engine: Engine) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    create_user(owner_engine, tenant_id)
    with app_engine.connect() as conn:
        assert _count(conn, tenants) == 0
        assert _count(conn, users) == 0
        assert _count(conn, sessions) == 0


def test_tenant_context_sees_only_own_rows(owner_engine: Engine, app_engine: Engine) -> None:
    tenant_a, _ = create_tenant(owner_engine)
    tenant_b, _ = create_tenant(owner_engine)
    user_a, _ = create_user(owner_engine, tenant_a)
    user_b, _ = create_user(owner_engine, tenant_b)
    with tenant_transaction(app_engine, tenant_a) as conn:
        user_ids = set(conn.execute(select(users.c.id)).scalars())
        tenant_ids = set(conn.execute(select(tenants.c.id)).scalars())
    assert user_a in user_ids
    assert user_b not in user_ids
    assert tenant_ids == {tenant_a}


def test_cannot_insert_rows_into_another_tenant(owner_engine: Engine, app_engine: Engine) -> None:
    tenant_a, _ = create_tenant(owner_engine)
    tenant_b, _ = create_tenant(owner_engine)
    with pytest.raises(ProgrammingError), tenant_transaction(app_engine, tenant_a) as conn:
        conn.execute(
            insert(users).values(
                tenant_id=tenant_b, email="x@y.test", password_hash="h", role="member"
            )
        )


def test_tenant_context_does_not_leak_into_the_next_transaction(
    owner_engine: Engine, app_engine: Engine
) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    create_user(owner_engine, tenant_id)
    with tenant_transaction(app_engine, tenant_id) as conn:
        assert _count(conn, users) >= 1
    with app_engine.connect() as conn:
        assert _count(conn, users) == 0


def test_tenant_transaction_requires_a_uuid(app_engine: Engine) -> None:
    with pytest.raises(TypeError), tenant_transaction(app_engine, str(uuid.uuid4())):  # type: ignore[arg-type]
        pass


def test_malformed_tenant_setting_fails_closed(app_engine: Engine) -> None:
    with pytest.raises(DataError), app_engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', 'not-a-uuid', true)"))
        conn.execute(select(users.c.id)).all()


def test_app_role_cannot_create_tenants(app_engine: Engine) -> None:
    new_id = uuid.uuid4()
    with pytest.raises(ProgrammingError), tenant_transaction(app_engine, new_id) as conn:
        conn.execute(insert(tenants).values(id=new_id, slug=f"x-{new_id.hex[:8]}", name="X"))


def test_app_role_cannot_delete_users(owner_engine: Engine, app_engine: Engine) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    user_id, _ = create_user(owner_engine, tenant_id)
    with pytest.raises(ProgrammingError), tenant_transaction(app_engine, tenant_id) as conn:
        conn.execute(delete(users).where(users.c.id == user_id))


@pytest.mark.parametrize("column", ["tenant_id", "email", "password_hash"])
def test_app_role_cannot_rewrite_protected_user_columns(
    owner_engine: Engine, app_engine: Engine, column: str
) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    other_tenant, _ = create_tenant(owner_engine)
    user_id, _ = create_user(owner_engine, tenant_id)
    value = {"tenant_id": other_tenant, "email": "new@x.test", "password_hash": "h"}[column]
    with pytest.raises(ProgrammingError), tenant_transaction(app_engine, tenant_id) as conn:
        conn.execute(update(users).where(users.c.id == user_id).values({column: value}))
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/integration/test_schema.py tests/security/test_rls_isolation.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.db.schema'`.

- [ ] **Step 4: Migration** — `backend/migrations/versions/0001_tenants_users_sessions.py`:

```python
"""Tenants, users, sessions with row-level security.

Revision ID: 0001
Revises:
Create Date: 2026-10-16
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _rls(table: str, tenant_column: str) -> list[str]:
    """App role sees only the current tenant; the owner (migrations and SECURITY DEFINER
    functions) sees all rows. FORCE makes the policies apply to the owner as well."""
    predicate = f"{tenant_column} = app_current_tenant()"
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {table}_tenant_isolation ON {table} FOR ALL TO veyl_app "
        f"USING ({predicate}) WITH CHECK ({predicate})",
        f"CREATE POLICY {table}_owner_all ON {table} FOR ALL TO veyl_owner "
        "USING (true) WITH CHECK (true)",
    ]


UPGRADE = [
    "CREATE TYPE user_role AS ENUM ('member', 'manager', 'admin')",
    # NULLIF: after a transaction-local setting has been used, Postgres reports ''
    # rather than NULL; ''::uuid would raise instead of matching nothing.
    """
    CREATE FUNCTION app_current_tenant() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$ SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid $$
    """,
    "GRANT EXECUTE ON FUNCTION app_current_tenant() TO veyl_app",
    """
    CREATE TABLE tenants (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        slug text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
        name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
        policy_version bigint NOT NULL DEFAULT 1,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE users (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
        email citext NOT NULL CHECK (length(email) BETWEEN 3 AND 320),
        password_hash text NOT NULL,
        role user_role NOT NULL,
        is_active boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, email),
        UNIQUE (tenant_id, id)
    )
    """,
    """
    CREATE TABLE sessions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL,
        user_id uuid NOT NULL,
        token_hash bytea NOT NULL UNIQUE CHECK (octet_length(token_hash) = 32),
        created_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL,
        revoked_at timestamptz,
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX sessions_tenant_user_idx ON sessions (tenant_id, user_id)",
    *_rls("tenants", "id"),
    *_rls("users", "tenant_id"),
    *_rls("sessions", "tenant_id"),
    "GRANT SELECT, UPDATE (name, policy_version) ON tenants TO veyl_app",
    "GRANT SELECT, INSERT, UPDATE (role, is_active) ON users TO veyl_app",
    "GRANT SELECT, INSERT, UPDATE (revoked_at) ON sessions TO veyl_app",
]

DOWNGRADE = [
    "DROP TABLE sessions",
    "DROP TABLE users",
    "DROP TABLE tenants",
    "DROP FUNCTION app_current_tenant()",
    "DROP TYPE user_role",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
```

If ruff flags `S608` on the f-strings in `_rls`, add `# noqa: S608` to those lines: inputs are compile-time constants.

- [ ] **Step 5: Core schema** — `backend/app/db/schema.py`:

```python
"""SQLAlchemy Core definitions mirroring the hand-written migrations.
`tests/integration/test_schema.py` fails if the two drift apart."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    LargeBinary,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import CITEXT, ENUM, UUID

from app.authz import Role

metadata = MetaData()

user_role = ENUM(*[r.value for r in Role], name="user_role", create_type=False)


def _id() -> Column[object]:
    return Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


def _created_at() -> Column[object]:
    return Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now())


tenants = Table(
    "tenants",
    metadata,
    _id(),
    Column("slug", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("policy_version", BigInteger, nullable=False, server_default=text("1")),
    _created_at(),
)

users = Table(
    "users",
    metadata,
    _id(),
    Column("tenant_id", UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
    Column("email", CITEXT, nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("role", user_role, nullable=False),
    Column("is_active", Boolean, nullable=False, server_default=true()),
    _created_at(),
    UniqueConstraint("tenant_id", "email"),
    UniqueConstraint("tenant_id", "id"),
)

sessions = Table(
    "sessions",
    metadata,
    _id(),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("token_hash", LargeBinary, nullable=False, unique=True),
    _created_at(),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    ForeignKeyConstraint(
        ["tenant_id", "user_id"], ["users.tenant_id", "users.id"], ondelete="CASCADE"
    ),
)
```

- [ ] **Step 6: Tenant transaction helper** — `backend/app/db/tenancy.py`:

```python
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import Connection, Engine, text


@contextmanager
def tenant_transaction(engine: Engine, tenant_id: UUID) -> Iterator[Connection]:
    """Open a transaction scoped to one tenant for row-level security.

    The setting is transaction-local, so a pooled connection never carries a previous
    request's tenant. `tenant_id` must come from the server-side AuthContext; the UUID
    type check rejects raw client strings."""
    if not isinstance(tenant_id, UUID):
        raise TypeError("tenant_id must be a UUID from the server-side auth context")
    with engine.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
        yield conn
```

- [ ] **Step 7: Run tests + lint + types**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all PASS. If the `Column[object]` annotations upset mypy, use `Column[Any]` with `from typing import Any`.

- [ ] **Step 8: README (data model + RLS paragraph), devlog, commit, push**

```bash
git add -A
git commit -m "feat(db): tenants, users, sessions with forced row-level security"
git push origin main
```

---

### Task 5: Definer functions, tenant provisioning, operator CLI

**Files:**
- Create: `backend/migrations/versions/0002_auth_functions.py`, `backend/app/provisioning.py`, `backend/app/cli.py`, `backend/tests/integration/test_auth_functions.py`, `backend/tests/integration/test_provisioning.py`
- Modify: `backend/tests/factories.py` (add `create_session`), `README.md`, `docs/devlog.md`

**Interfaces:**
- Consumes: schema (Task 4), `hash_password`, `hash_token`, `MIN_PASSWORD_LENGTH` (Task 3).
- Produces:
  - SQL `auth_resolve_login(p_tenant_slug text, p_email text) RETURNS TABLE (user_id uuid, tenant_id uuid, password_hash text, role user_role, is_active boolean)`.
  - SQL `auth_resolve_session(p_token_hash bytea) RETURNS TABLE (session_id uuid, user_id uuid, tenant_id uuid, role user_role)` — only unexpired, unrevoked sessions of active users.
  - `app.provisioning.provision_tenant(owner_engine: Engine, *, slug: str, name: str, admin_email: str, admin_password: str) -> ProvisionedTenant(tenant_id: UUID, admin_user_id: UUID)`; raises `ValueError` on bad slug, short password, duplicate slug.
  - `app.cli.main(argv: Sequence[str] | None = None) -> int`; command `provision-tenant --slug --name --admin-email`, password from env `VEYL_ADMIN_PASSWORD`, DB from env `MIGRATIONS_DATABASE_URL`.
  - `tests.factories.create_session(owner_engine, tenant_id, user_id, *, expires_in: timedelta = timedelta(hours=1), revoked: bool = False) -> str` (returns the raw token).

- [ ] **Step 1: Factory helper** — append to `backend/tests/factories.py`:

```python
from datetime import timedelta

from sqlalchemy import func

from app.auth.tokens import hash_token, new_session_token
from app.db.schema import sessions


def create_session(
    owner_engine: Engine,
    tenant_id: UUID,
    user_id: UUID,
    *,
    expires_in: timedelta = timedelta(hours=1),
    revoked: bool = False,
) -> str:
    token = new_session_token()
    with owner_engine.begin() as conn:
        conn.execute(
            insert(sessions).values(
                tenant_id=tenant_id,
                user_id=user_id,
                token_hash=hash_token(token),
                expires_at=func.now() + expires_in,
                revoked_at=func.now() if revoked else None,
            )
        )
    return token
```
(Move the new imports to the top of the file with the others.)

- [ ] **Step 2: Write failing tests**

`backend/tests/integration/test_auth_functions.py`:
```python
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, text, update

from app.auth.tokens import hash_token
from app.authz import Role
from app.db.schema import users
from tests.factories import create_session, create_tenant, create_user

pytestmark = pytest.mark.integration

DEFINER_FUNCTIONS_FOR_APP = {"auth_resolve_login", "auth_resolve_session"}


def _login_row(app_engine: Engine, slug: str, email: str) -> Any:
    with app_engine.connect() as conn:
        return conn.execute(
            text("SELECT * FROM auth_resolve_login(:slug, :email)"), {"slug": slug, "email": email}
        ).one_or_none()


def _session_row(app_engine: Engine, token: str) -> Any:
    with app_engine.connect() as conn:
        return conn.execute(
            text("SELECT * FROM auth_resolve_session(:h)"), {"h": hash_token(token)}
        ).one_or_none()


def test_resolve_login_matches_email_case_insensitively(owner_engine: Engine, app_engine: Engine) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    user_id, email = create_user(owner_engine, tenant_id, role=Role.MANAGER)
    row = _login_row(app_engine, slug, email.upper())
    assert row is not None
    assert (row.user_id, row.tenant_id, row.role, row.is_active) == (user_id, tenant_id, "manager", True)
    assert row.password_hash.startswith("$argon2id$")


def test_resolve_login_is_scoped_to_the_named_tenant(owner_engine: Engine, app_engine: Engine) -> None:
    _, slug_a = create_tenant(owner_engine)
    tenant_b, _ = create_tenant(owner_engine)
    _, email_b = create_user(owner_engine, tenant_b)
    assert _login_row(app_engine, slug_a, email_b) is None


def test_resolve_session_returns_live_sessions(owner_engine: Engine, app_engine: Engine) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    user_id, _ = create_user(owner_engine, tenant_id, role=Role.ADMIN)
    row = _session_row(app_engine, create_session(owner_engine, tenant_id, user_id))
    assert row is not None
    assert (row.user_id, row.tenant_id, row.role) == (user_id, tenant_id, "admin")


@pytest.mark.parametrize(
    "kwargs", [{"revoked": True}, {"expires_in": timedelta(seconds=-1)}], ids=["revoked", "expired"]
)
def test_resolve_session_rejects_dead_sessions(
    owner_engine: Engine, app_engine: Engine, kwargs: dict[str, Any]
) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    user_id, _ = create_user(owner_engine, tenant_id)
    assert _session_row(app_engine, create_session(owner_engine, tenant_id, user_id, **kwargs)) is None


def test_resolve_session_rejects_inactive_users(owner_engine: Engine, app_engine: Engine) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    user_id, _ = create_user(owner_engine, tenant_id)
    token = create_session(owner_engine, tenant_id, user_id)
    with owner_engine.begin() as conn:
        conn.execute(update(users).where(users.c.id == user_id).values(is_active=False))
    assert _session_row(app_engine, token) is None


def test_resolve_session_rejects_unknown_tokens(app_engine: Engine) -> None:
    assert _session_row(app_engine, "never-issued") is None


def _definer_functions(owner_engine: Engine) -> list[Any]:
    with owner_engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT p.proname, p.proconfig, "
                "has_function_privilege('veyl_app', p.oid, 'EXECUTE') AS app_can_execute "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public' AND p.prosecdef"
            )
        ).all()


def test_security_definer_functions_pin_search_path(owner_engine: Engine) -> None:
    rows = _definer_functions(owner_engine)
    assert {r.proname for r in rows} >= DEFINER_FUNCTIONS_FOR_APP
    for r in rows:
        assert r.proconfig is not None and "search_path=public, pg_temp" in r.proconfig, r.proname


def test_app_role_executes_only_the_intended_definer_functions(owner_engine: Engine) -> None:
    executable = {r.proname for r in _definer_functions(owner_engine) if r.app_can_execute}
    assert executable == DEFINER_FUNCTIONS_FOR_APP
```

`backend/tests/integration/test_provisioning.py`:
```python
import uuid

import pytest
from sqlalchemy import Engine, text

from app import cli
from app.auth.passwords import verify_password
from app.provisioning import provision_tenant
from tests.conftest import DatabaseUrls

pytestmark = pytest.mark.integration

PASSWORD = "a-strong-password-123"


def _slug() -> str:
    return f"p-{uuid.uuid4().hex[:10]}"


def test_provisioned_admin_can_be_resolved_for_login(owner_engine: Engine, app_engine: Engine) -> None:
    slug = _slug()
    result = provision_tenant(
        owner_engine, slug=slug, name="Prov Co", admin_email=" Admin@Prov.test ", admin_password=PASSWORD
    )
    with app_engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM auth_resolve_login(:s, :e)"), {"s": slug, "e": "admin@prov.test"}
        ).one()
    assert (row.user_id, row.tenant_id, row.role) == (result.admin_user_id, result.tenant_id, "admin")
    assert verify_password(row.password_hash, PASSWORD)


@pytest.mark.parametrize(
    ("slug", "password"),
    [("Bad Slug!", PASSWORD), ("ok-slug", "short")],
    ids=["bad-slug", "short-password"],
)
def test_provision_rejects_invalid_input(owner_engine: Engine, slug: str, password: str) -> None:
    with pytest.raises(ValueError):
        provision_tenant(owner_engine, slug=slug, name="X", admin_email="a@b.test", admin_password=password)


def test_provision_rejects_duplicate_slug(owner_engine: Engine) -> None:
    slug = _slug()
    provision_tenant(owner_engine, slug=slug, name="One", admin_email="a@one.test", admin_password=PASSWORD)
    with pytest.raises(ValueError, match="already exists"):
        provision_tenant(owner_engine, slug=slug, name="Two", admin_email="a@two.test", admin_password=PASSWORD)


def test_cli_provisions_with_owner_url_and_env_password(
    database_urls: DatabaseUrls, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MIGRATIONS_DATABASE_URL", database_urls.owner)
    monkeypatch.setenv("VEYL_ADMIN_PASSWORD", PASSWORD)
    code = cli.main(["provision-tenant", "--slug", _slug(), "--name", "CLI Co", "--admin-email", "root@cli.test"])
    assert code == 0
    assert "tenant_id=" in capsys.readouterr().out


def test_cli_refuses_without_password_env(
    database_urls: DatabaseUrls, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MIGRATIONS_DATABASE_URL", database_urls.owner)
    monkeypatch.delenv("VEYL_ADMIN_PASSWORD", raising=False)
    code = cli.main(["provision-tenant", "--slug", _slug(), "--name", "X", "--admin-email", "a@b.test"])
    assert code == 2
    assert "VEYL_ADMIN_PASSWORD" in capsys.readouterr().err
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/integration/test_auth_functions.py tests/integration/test_provisioning.py -q`
Expected: FAIL — `function auth_resolve_login(...) does not exist` and `No module named 'app.provisioning'`.

- [ ] **Step 4: Migration** — `backend/migrations/versions/0002_auth_functions.py`:

```python
"""Cross-tenant lookups for authentication, as narrow SECURITY DEFINER functions.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-16
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE = [
    """
    CREATE FUNCTION auth_resolve_login(p_tenant_slug text, p_email text)
    RETURNS TABLE (user_id uuid, tenant_id uuid, password_hash text, role user_role, is_active boolean)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = public, pg_temp
    AS $$
        SELECT u.id, u.tenant_id, u.password_hash, u.role, u.is_active
        FROM users u
        JOIN tenants t ON t.id = u.tenant_id
        WHERE t.slug = p_tenant_slug AND u.email = p_email::citext
    $$
    """,
    "REVOKE ALL ON FUNCTION auth_resolve_login(text, text) FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION auth_resolve_login(text, text) TO veyl_app",
    """
    CREATE FUNCTION auth_resolve_session(p_token_hash bytea)
    RETURNS TABLE (session_id uuid, user_id uuid, tenant_id uuid, role user_role)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = public, pg_temp
    AS $$
        SELECT s.id, u.id, u.tenant_id, u.role
        FROM sessions s
        JOIN users u ON u.tenant_id = s.tenant_id AND u.id = s.user_id
        WHERE s.token_hash = p_token_hash
          AND s.revoked_at IS NULL
          AND s.expires_at > now()
          AND u.is_active
    $$
    """,
    "REVOKE ALL ON FUNCTION auth_resolve_session(bytea) FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION auth_resolve_session(bytea) TO veyl_app",
]

DOWNGRADE = [
    "DROP FUNCTION auth_resolve_session(bytea)",
    "DROP FUNCTION auth_resolve_login(text, text)",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
```

- [ ] **Step 5: Provisioning + CLI**

`backend/app/provisioning.py`:
```python
"""Operator-only tenant provisioning. Runs with the owner role (MIGRATIONS_DATABASE_URL);
there is deliberately no API for creating tenants."""

import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Engine, insert
from sqlalchemy.exc import IntegrityError

from app.auth.passwords import MIN_PASSWORD_LENGTH, hash_password
from app.authz import Role
from app.db.schema import tenants, users

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


@dataclass(frozen=True, slots=True)
class ProvisionedTenant:
    tenant_id: UUID
    admin_user_id: UUID


def provision_tenant(
    owner_engine: Engine, *, slug: str, name: str, admin_email: str, admin_password: str
) -> ProvisionedTenant:
    if not SLUG_PATTERN.fullmatch(slug):
        raise ValueError("slug must be 2-63 chars of lowercase letters, digits, and hyphens")
    if len(admin_password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"admin password must be at least {MIN_PASSWORD_LENGTH} characters")
    password_hash = hash_password(admin_password)
    try:
        with owner_engine.begin() as conn:
            tenant_id = conn.execute(
                insert(tenants).values(slug=slug, name=name).returning(tenants.c.id)
            ).scalar_one()
            admin_user_id = conn.execute(
                insert(users)
                .values(
                    tenant_id=tenant_id,
                    email=admin_email.strip().lower(),
                    password_hash=password_hash,
                    role=Role.ADMIN.value,
                )
                .returning(users.c.id)
            ).scalar_one()
    except IntegrityError as exc:
        raise ValueError(f"tenant slug {slug!r} already exists") from exc
    return ProvisionedTenant(tenant_id=tenant_id, admin_user_id=admin_user_id)
```

`backend/app/cli.py`:
```python
"""Operator commands: python -m app.cli <command>."""

import argparse
import os
import sys
from collections.abc import Sequence

from sqlalchemy import create_engine

from app.provisioning import provision_tenant


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Veyl operator commands.")
    commands = parser.add_subparsers(dest="command", required=True)
    provision = commands.add_parser(
        "provision-tenant",
        help="Create a tenant and its first admin. Password comes from VEYL_ADMIN_PASSWORD.",
    )
    provision.add_argument("--slug", required=True)
    provision.add_argument("--name", required=True)
    provision.add_argument("--admin-email", required=True)
    args = parser.parse_args(argv)

    database_url = os.environ.get("MIGRATIONS_DATABASE_URL")
    password = os.environ.get("VEYL_ADMIN_PASSWORD")
    if not database_url:
        print("MIGRATIONS_DATABASE_URL must be set (owner role).", file=sys.stderr)
        return 2
    if not password:
        print("VEYL_ADMIN_PASSWORD must be set; passwords are never taken as arguments.", file=sys.stderr)
        return 2

    engine = create_engine(database_url)
    try:
        result = provision_tenant(
            engine, slug=args.slug, name=args.name, admin_email=args.admin_email, admin_password=password
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(f"tenant_id={result.tenant_id} admin_user_id={result.admin_user_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Run full suite + lint + types** — Expected: PASS, clean.

- [ ] **Step 7: README ("Provisioning a tenant" section with the `docker compose run` command below), devlog, commit, push**

````markdown
### Provisioning a tenant

Tenants are created by an operator, never through the API:

```bash
docker compose run --rm -e VEYL_ADMIN_PASSWORD='choose-a-long-password' migrate \
  python -m app.cli provision-tenant --slug acme --name "Acme Corp" --admin-email alice@acme.test
```
````

```bash
git add -A
git commit -m "feat(auth): definer lookups for login and sessions; operator tenant provisioning"
git push origin main
```

---

### Task 6: Authentication API — login, logout, me, CSRF

**Files:**
- Create: `backend/app/auth/service.py`, `backend/app/auth/deps.py`, `backend/app/auth/router.py`, `backend/tests/api.py`, `backend/tests/integration/test_auth_api.py`, `backend/tests/security/test_auth_sessions.py`, `backend/tests/security/test_fail_closed.py`
- Modify: `backend/app/main.py` (limiters + router), `README.md`, `docs/devlog.md`

**Interfaces:**
- Consumes: Tasks 1-5.
- Produces:
  - Cookies: `veyl_session` (HttpOnly, SameSite=Lax, Path=/, Secure in production), `veyl_csrf` (readable by JS, same attributes otherwise). Header `X-CSRF-Token` must equal `veyl_csrf` on POST/PUT/PATCH/DELETE (except login).
  - `app.auth.deps`: `get_engine(request) -> Engine`, `get_settings(request) -> Settings`, `get_auth_context(request) -> AuthContext`, `require_csrf(request) -> None`; aliases `EngineDep`, `SettingsDep`, `CurrentUser`.
  - `app.auth.service`: `Identity(user_id, email, role, tenant_id, tenant_slug, tenant_name)`, `login(...) -> LoginResult(session_token, csrf_token, identity)`, `logout(engine, ctx)`, `resolve_session(engine, token) -> AuthContext | None`, `load_identity(engine, ctx) -> Identity`.
  - `POST /api/v1/auth/login` `{tenant_slug, email, password}` → 200 `MeResponse`; `POST /api/v1/auth/logout` → 204; `GET /api/v1/auth/me` → `MeResponse {user: {id, email, role}, tenant: {id, slug, name}, permissions: [str]}`.
  - `tests.api`: `client_for(database_url: str, **settings) -> TestClient`, `login(client, slug, email, password=DEFAULT_PASSWORD) -> httpx Response`, `csrf(client) -> dict[str, str]`.

- [ ] **Step 1: Test helpers** — `backend/tests/api.py`:

```python
from typing import Any

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.factories import DEFAULT_PASSWORD

API = "/api/v1"


def client_for(database_url: str, **settings: Any) -> TestClient:
    """Use as a context manager so the app lifespan (engine) runs."""
    return TestClient(create_app(Settings(database_url=database_url, **settings)))


def login(client: TestClient, slug: str, email: str, password: str = DEFAULT_PASSWORD) -> Any:
    return client.post(
        f"{API}/auth/login", json={"tenant_slug": slug, "email": email, "password": password}
    )


def csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("veyl_csrf") or ""}


def set_cookies(response: Any) -> list[str]:
    return [v for k, v in response.headers.multi_items() if k.lower() == "set-cookie"]
```

- [ ] **Step 2: Write failing tests**

`backend/tests/integration/test_auth_api.py`:
```python
import pytest
from sqlalchemy import Engine

from app.authz import Role
from tests.api import API, client_for, csrf, login
from tests.conftest import DatabaseUrls
from tests.factories import create_tenant, create_user

pytestmark = pytest.mark.integration


def test_login_returns_identity_and_permission_hints(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    tenant_id, slug = create_tenant(owner_engine, name="Acme")
    user_id, email = create_user(owner_engine, tenant_id, role=Role.MANAGER)
    with client_for(database_urls.app) as client:
        r = login(client, slug, email)
    assert r.status_code == 200
    body = r.json()
    assert body["user"] == {"id": str(user_id), "email": email, "role": "manager"}
    assert body["tenant"] == {"id": str(tenant_id), "slug": slug, "name": "Acme"}
    assert body["permissions"] == ["document.upload", "rag.query", "tenant.read"]


def test_me_returns_the_current_identity(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    user_id, email = create_user(owner_engine, tenant_id)
    with client_for(database_urls.app) as client:
        login(client, slug, email)
        r = client.get(f"{API}/auth/me")
    assert r.status_code == 200
    assert r.json()["user"]["id"] == str(user_id)


def test_login_normalises_email_and_slug(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    _, email = create_user(owner_engine, tenant_id)
    with client_for(database_urls.app) as client:
        r = login(client, f" {slug.upper()} ", f"  {email.upper()} ")
    assert r.status_code == 200


def test_logout_revokes_the_session(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    _, email = create_user(owner_engine, tenant_id)
    with client_for(database_urls.app) as client:
        login(client, slug, email)
        old_token = client.cookies.get("veyl_session")
        assert client.post(f"{API}/auth/logout", headers=csrf(client)).status_code == 204
        client.cookies.set("veyl_session", old_token or "")
        assert client.get(f"{API}/auth/me").status_code == 401
```

`backend/tests/security/test_auth_sessions.py`:
```python
"""T15 (session theft, CSRF) and T16 (brute-force login)."""

import hashlib

import pytest
from sqlalchemy import Engine, select, text, update

from app.authz import Role
from app.db.schema import sessions, users
from tests.api import API, client_for, csrf, login, set_cookies
from tests.conftest import DatabaseUrls
from tests.factories import create_tenant, create_user

pytestmark = pytest.mark.integration


def _cookie(response, name: str) -> str:  # type: ignore[no-untyped-def]
    return next(c for c in set_cookies(response) if c.startswith(f"{name}="))


def test_session_cookie_is_httponly_lax_and_csrf_cookie_is_readable(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    _, email = create_user(owner_engine, tenant_id)
    with client_for(database_urls.app) as client:
        r = login(client, slug, email)
    session_cookie = _cookie(r, "veyl_session")
    csrf_cookie = _cookie(r, "veyl_csrf")
    assert "HttpOnly" in session_cookie
    assert "samesite=lax" in session_cookie.lower()
    assert "Path=/" in session_cookie
    assert "Secure" not in session_cookie
    assert "HttpOnly" not in csrf_cookie


def test_cookies_are_secure_in_production(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    _, email = create_user(owner_engine, tenant_id)
    with client_for(database_urls.app, environment="production") as client:
        r = login(client, slug, email)
    assert "Secure" in _cookie(r, "veyl_session")
    assert "Secure" in _cookie(r, "veyl_csrf")


def test_session_token_is_stored_only_as_a_sha256_hash(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    user_id, email = create_user(owner_engine, tenant_id)
    with client_for(database_urls.app) as client:
        login(client, slug, email)
        token = client.cookies.get("veyl_session") or ""
    with owner_engine.connect() as conn:
        stored = conn.execute(select(sessions.c.token_hash).where(sessions.c.user_id == user_id)).scalars().all()
    assert [bytes(h) for h in stored] == [hashlib.sha256(token.encode()).digest()]


def test_session_expiry_uses_database_clock(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    user_id, email = create_user(owner_engine, tenant_id)
    with client_for(database_urls.app, session_ttl_minutes=60) as client:
        login(client, slug, email)
    with owner_engine.connect() as conn:
        seconds = conn.execute(
            text("SELECT extract(epoch FROM expires_at - created_at) FROM sessions WHERE user_id = :u"),
            {"u": user_id},
        ).scalar_one()
    assert abs(float(seconds) - 3600) < 5


def test_login_failures_are_indistinguishable(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    _, email = create_user(owner_engine, tenant_id)
    _, inactive = create_user(owner_engine, tenant_id, is_active=False)
    attempts = [
        (slug, email, "wrong-password-123"),
        (slug, "nobody@example.test", "whatever-password"),
        ("no-such-tenant", email, "whatever-password"),
        (slug, inactive, "correct-horse-battery-staple"),
    ]
    bodies = []
    with client_for(database_urls.app) as client:
        for s, e, p in attempts:
            r = login(client, s, e, p)
            assert r.status_code == 401
            error = r.json()["error"]
            bodies.append((error["code"], error["message"]))
    assert len(set(bodies)) == 1
    assert bodies[0][0] == "invalid_credentials"


def test_unsafe_requests_require_matching_csrf_token(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    _, email = create_user(owner_engine, tenant_id)
    with client_for(database_urls.app) as client:
        login(client, slug, email)
        missing = client.post(f"{API}/auth/logout")
        wrong = client.post(f"{API}/auth/logout", headers={"X-CSRF-Token": "not-the-token"})
        assert (missing.status_code, missing.json()["error"]["code"]) == (403, "csrf_failed")
        assert (wrong.status_code, wrong.json()["error"]["code"]) == (403, "csrf_failed")
        assert client.post(f"{API}/auth/logout", headers=csrf(client)).status_code == 204


def test_non_ascii_csrf_header_is_rejected_not_crashed(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    _, email = create_user(owner_engine, tenant_id)
    with client_for(database_urls.app) as client:
        login(client, slug, email)
        r = client.post(f"{API}/auth/logout", headers={"X-CSRF-Token": b"\xc3\xa9t\xc3\xa9"})
    assert r.status_code == 403


def test_account_is_locked_after_repeated_failures(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    _, email = create_user(owner_engine, tenant_id)
    _, other = create_user(owner_engine, tenant_id)
    with client_for(database_urls.app, login_max_failures=3) as client:
        for _ in range(3):
            assert login(client, slug, email, "wrong-password-123").status_code == 401
        locked = login(client, slug, email)
        assert (locked.status_code, locked.json()["error"]["code"]) == (429, "rate_limited")
        assert login(client, slug, other).status_code == 200


def test_oversized_passwords_are_rejected_before_hashing(database_urls: DatabaseUrls) -> None:
    with client_for(database_urls.app) as client:
        r = login(client, "acme", "a@b.test", "x" * 5000)
    assert r.status_code == 422


def test_no_cors_headers_for_foreign_origins(database_urls: DatabaseUrls) -> None:
    with client_for(database_urls.app) as client:
        r = client.options(
            f"{API}/auth/login",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
```

`backend/tests/security/test_fail_closed.py`:
```python
"""T5 / brief test 5 (missing or invalid authorization context results in denial) and
T14 (fail closed when storage is unavailable)."""

from datetime import timedelta

import pytest
from sqlalchemy import Engine, update

from app.db.schema import users
from tests.api import API, client_for, login
from tests.conftest import DatabaseUrls
from tests.factories import create_session, create_tenant, create_user

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("cookie", [None, "", "garbage", "a" * 4000], ids=["missing", "empty", "garbage", "huge"])
def test_missing_or_invalid_session_is_denied(database_urls: DatabaseUrls, cookie: str | None) -> None:
    with client_for(database_urls.app) as client:
        if cookie is not None:
            client.cookies.set("veyl_session", cookie)
        r = client.get(f"{API}/auth/me")
    assert (r.status_code, r.json()["error"]["code"]) == (401, "unauthenticated")


@pytest.mark.parametrize(
    "kwargs", [{"expires_in": timedelta(seconds=-1)}, {"revoked": True}], ids=["expired", "revoked"]
)
def test_dead_sessions_are_denied(owner_engine: Engine, database_urls: DatabaseUrls, kwargs: dict) -> None:  # type: ignore[type-arg]
    tenant_id, _ = create_tenant(owner_engine)
    user_id, _ = create_user(owner_engine, tenant_id)
    token = create_session(owner_engine, tenant_id, user_id, **kwargs)
    with client_for(database_urls.app) as client:
        client.cookies.set("veyl_session", token)
        assert client.get(f"{API}/auth/me").status_code == 401


def test_deactivated_users_lose_access_on_next_request(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    tenant_id, slug = create_tenant(owner_engine)
    user_id, email = create_user(owner_engine, tenant_id)
    with client_for(database_urls.app) as client:
        login(client, slug, email)
        assert client.get(f"{API}/auth/me").status_code == 200
        with owner_engine.begin() as conn:
            conn.execute(update(users).where(users.c.id == user_id).values(is_active=False))
        assert client.get(f"{API}/auth/me").status_code == 401


def test_database_outage_fails_closed() -> None:
    with client_for("postgresql+psycopg://veyl_app:x@127.0.0.1:1/veyl") as client:
        client.cookies.set("veyl_session", "some-token")
        r = client.get(f"{API}/auth/me")
    assert (r.status_code, r.json()["error"]["code"]) == (503, "service_unavailable")
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/integration/test_auth_api.py tests/security/test_auth_sessions.py tests/security/test_fail_closed.py -q`
Expected: FAIL — 404 on `/api/v1/auth/*` (router not mounted).

- [ ] **Step 4: Implement**

`backend/app/auth/service.py`:
```python
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import Connection, Engine, func, insert, select, text, update

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
) -> LoginResult:
    slug = tenant_slug.strip().lower()
    email = email.strip().lower()
    account_key = f"account:{slug}:{email}"
    ip_key = f"ip:{client_ip}"
    if limiters.account.is_blocked(account_key) or limiters.ip.is_blocked(ip_key):
        raise RateLimited()

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM auth_resolve_login(:slug, :email)"), {"slug": slug, "email": email}
        ).one_or_none()

    # Always verify (dummy hash for unknown users) so timing does not reveal accounts.
    password_ok = verify_password(row.password_hash if row else None, password)
    if row is None or not row.is_active or not password_ok:
        limiters.account.record_failure(account_key)
        limiters.ip.record_failure(ip_key)
        raise InvalidCredentials()
    limiters.account.reset(account_key)

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
    return LoginResult(session_token=token, csrf_token=new_csrf_token(), identity=identity)


def resolve_session(engine: Engine, token: str) -> AuthContext | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM auth_resolve_session(:token_hash)"), {"token_hash": hash_token(token)}
        ).one_or_none()
    if row is None:
        return None
    return AuthContext(
        user_id=row.user_id, tenant_id=row.tenant_id, role=Role(row.role), session_id=row.session_id
    )


def load_identity(engine: Engine, ctx: AuthContext) -> Identity:
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        return _load_identity(conn, ctx.user_id)


def logout(engine: Engine, ctx: AuthContext) -> None:
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        conn.execute(
            update(sessions).where(sessions.c.id == ctx.session_id).values(revoked_at=func.now())
        )
```

`backend/app/auth/deps.py`:
```python
import hmac
from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy import Engine

from app.auth.service import resolve_session
from app.authz import AuthContext
from app.config import Settings
from app.errors import CsrfFailed, Unauthenticated

SESSION_COOKIE = "veyl_session"
CSRF_COOKIE = "veyl_csrf"
CSRF_HEADER = "X-CSRF-Token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_engine(request: Request) -> Engine:
    return cast(Engine, request.app.state.engine)


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_auth_context(request: Request) -> AuthContext:
    """The only source of identity: the session cookie, resolved in the database.
    Database errors propagate and become 503 (fail closed)."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise Unauthenticated()
    ctx = resolve_session(get_engine(request), token)
    if ctx is None:
        raise Unauthenticated()
    return ctx


def require_csrf(request: Request) -> None:
    """Double-submit check for state-changing requests."""
    if request.method in _SAFE_METHODS:
        return
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get(CSRF_HEADER, "")
    # Compare bytes: compare_digest raises TypeError on non-ASCII str.
    if not cookie or not hmac.compare_digest(cookie.encode(), header.encode()):
        raise CsrfFailed()


EngineDep = Annotated[Engine, Depends(get_engine)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
CurrentUser = Annotated[AuthContext, Depends(get_auth_context)]
```

`backend/app/auth/router.py`:
```python
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.auth import service
from app.auth.deps import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    CurrentUser,
    EngineDep,
    SettingsDep,
    require_csrf,
)
from app.auth.passwords import MAX_PASSWORD_LENGTH
from app.authz import Role, permissions

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_slug: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class MeUser(BaseModel):
    id: UUID
    email: str
    role: Role


class MeTenant(BaseModel):
    id: UUID
    slug: str
    name: str


class MeResponse(BaseModel):
    user: MeUser
    tenant: MeTenant
    permissions: list[str]


def _me(identity: service.Identity) -> MeResponse:
    return MeResponse(
        user=MeUser(id=identity.user_id, email=identity.email, role=identity.role),
        tenant=MeTenant(id=identity.tenant_id, slug=identity.tenant_slug, name=identity.tenant_name),
        permissions=permissions(identity.role),
    )


@router.post("/login")
def login(
    body: LoginRequest, request: Request, response: Response, engine: EngineDep, settings: SettingsDep
) -> MeResponse:
    # No CSRF token yet at login: JSON-only body, no CORS, and SameSite=Lax cookies
    # prevent cross-site submission (docs/decisions.md).
    result = service.login(
        engine,
        request.app.state.login_limiters,
        tenant_slug=body.tenant_slug,
        email=body.email,
        password=body.password,
        client_ip=request.client.host if request.client else "unknown",
        session_ttl=timedelta(minutes=settings.session_ttl_minutes),
    )
    max_age = settings.session_ttl_minutes * 60
    common = {"max_age": max_age, "path": "/", "secure": settings.cookie_secure, "samesite": "lax"}
    response.set_cookie(SESSION_COOKIE, result.session_token, httponly=True, **common)  # type: ignore[arg-type]
    response.set_cookie(CSRF_COOKIE, result.csrf_token, httponly=False, **common)  # type: ignore[arg-type]
    return _me(result.identity)


@router.post("/logout", status_code=204, dependencies=[Depends(require_csrf)])
def logout(ctx: CurrentUser, engine: EngineDep, settings: SettingsDep) -> Response:
    service.logout(engine, ctx)
    response = Response(status_code=204)
    for name, httponly in ((SESSION_COOKIE, True), (CSRF_COOKIE, False)):
        response.delete_cookie(
            name, path="/", secure=settings.cookie_secure, httponly=httponly, samesite="lax"
        )
    return response


@router.get("/me")
def me(ctx: CurrentUser, engine: EngineDep) -> MeResponse:
    return _me(service.load_identity(engine, ctx))
```

In `backend/app/main.py`: import `from app.auth import router as auth_router` and `from app.auth.ratelimit import LoginLimiters`; after `app.state.settings = settings` add
```python
    app.state.login_limiters = LoginLimiters.create(
        max_failures=settings.login_max_failures, window_seconds=settings.login_window_seconds
    )
```
and after the health router: `app.include_router(auth_router.router, prefix=API_PREFIX)`.

- [ ] **Step 5: Run full suite + lint + types** — Expected: PASS, clean.

- [ ] **Step 6: README (auth endpoints, cookie/CSRF model), devlog, commit, push**

```bash
git add -A
git commit -m "feat(auth): session login/logout/me with CSRF double-submit and lockout"
git push origin main
```

---

### Task 7: Tenant and user management API

**Files:**
- Create: `backend/app/tenants/__init__.py` (empty), `backend/app/tenants/router.py`, `backend/app/users/__init__.py` (empty), `backend/app/users/service.py`, `backend/app/users/router.py`, `backend/tests/integration/test_users_api.py`, `backend/tests/security/test_privilege_escalation.py`, `backend/tests/security/test_forged_identifiers.py`, `backend/tests/security/test_tenant_isolation.py`
- Modify: `backend/app/main.py` (routers), `backend/tests/factories.py` (add `create_tenant_with_admin`), `README.md`, `docs/devlog.md`

**Interfaces:**
- Consumes: Tasks 1-6.
- Produces:
  - `GET /api/v1/tenant` → `{id, slug, name}`; `PATCH /api/v1/tenant` `{name}` (admin, CSRF).
  - `GET /api/v1/users` → `[{id, email, role, is_active, created_at}]` (admin); `POST /api/v1/users` `{email, password, role}` → 201 (admin, CSRF); `PATCH /api/v1/users/{user_id}` `{role?, is_active?}` (admin, CSRF; not self).
  - Every role/active change bumps `tenants.policy_version`; deactivation revokes the user's live sessions; a tenant always keeps at least one active admin.
  - `app.users.service`: `UserRecord`, `list_users(engine, ctx)`, `create_user(engine, ctx, *, email, password, role)`, `update_user(engine, ctx, user_id, *, role, is_active)`.
  - `tests.factories.create_tenant_with_admin(owner_engine) -> SeededTenant(tenant_id, slug, admin_id, admin_email)`.

- [ ] **Step 1: Factory** — append to `backend/tests/factories.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class SeededTenant:
    tenant_id: UUID
    slug: str
    admin_id: UUID
    admin_email: str


def create_tenant_with_admin(owner_engine: Engine, *, name: str = "Test Tenant") -> SeededTenant:
    tenant_id, slug = create_tenant(owner_engine, name=name)
    admin_id, admin_email = create_user(owner_engine, tenant_id, role=Role.ADMIN)
    return SeededTenant(tenant_id, slug, admin_id, admin_email)
```

- [ ] **Step 2: Write failing tests**

`backend/tests/integration/test_users_api.py`:
```python
import threading
from uuid import UUID

import pytest
from sqlalchemy import Engine, select

from app.authz import AuthContext, Role
from app.db.schema import sessions, tenants, users
from app.errors import Conflict
from app.users import service as user_service
from tests.api import API, client_for, csrf, login
from tests.conftest import DatabaseUrls
from tests.factories import create_tenant_with_admin, create_user

pytestmark = pytest.mark.integration


def _policy_version(owner_engine: Engine, tenant_id: UUID) -> int:
    with owner_engine.connect() as conn:
        return int(conn.execute(select(tenants.c.policy_version).where(tenants.c.id == tenant_id)).scalar_one())


def test_admin_lists_only_own_tenant_users(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    a = create_tenant_with_admin(owner_engine)
    b = create_tenant_with_admin(owner_engine)
    member_a, _ = create_user(owner_engine, a.tenant_id)
    with client_for(database_urls.app) as client:
        login(client, a.slug, a.admin_email)
        ids = {u["id"] for u in client.get(f"{API}/users").json()}
    assert ids == {str(a.admin_id), str(member_a)}
    assert str(b.admin_id) not in ids


def test_admin_creates_a_user_who_can_log_in(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    a = create_tenant_with_admin(owner_engine)
    with client_for(database_urls.app) as admin:
        login(admin, a.slug, a.admin_email)
        r = admin.post(
            f"{API}/users",
            json={"email": "New.Person@Acme.test", "password": "another-long-password", "role": "member"},
            headers=csrf(admin),
        )
    assert r.status_code == 201
    assert r.json()["email"] == "new.person@acme.test"
    with client_for(database_urls.app) as newbie:
        assert login(newbie, a.slug, "new.person@acme.test", "another-long-password").status_code == 200


def test_create_user_rejects_case_insensitive_duplicate(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    a = create_tenant_with_admin(owner_engine)
    with client_for(database_urls.app) as admin:
        login(admin, a.slug, a.admin_email)
        r = admin.post(
            f"{API}/users",
            json={"email": a.admin_email.upper(), "password": "another-long-password", "role": "member"},
            headers=csrf(admin),
        )
    assert (r.status_code, r.json()["error"]["code"]) == (409, "conflict")


def test_create_user_rejects_short_password(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    a = create_tenant_with_admin(owner_engine)
    with client_for(database_urls.app) as admin:
        login(admin, a.slug, a.admin_email)
        r = admin.post(
            f"{API}/users", json={"email": "x@y.test", "password": "short", "role": "member"}, headers=csrf(admin)
        )
    assert r.status_code == 422


def test_role_change_bumps_policy_version(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    a = create_tenant_with_admin(owner_engine)
    member, _ = create_user(owner_engine, a.tenant_id)
    before = _policy_version(owner_engine, a.tenant_id)
    with client_for(database_urls.app) as admin:
        login(admin, a.slug, a.admin_email)
        r = admin.patch(f"{API}/users/{member}", json={"role": "manager"}, headers=csrf(admin))
    assert r.status_code == 200
    assert r.json()["role"] == "manager"
    assert _policy_version(owner_engine, a.tenant_id) == before + 1


def test_deactivation_revokes_sessions(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    a = create_tenant_with_admin(owner_engine)
    member, member_email = create_user(owner_engine, a.tenant_id)
    with client_for(database_urls.app) as admin, client_for(database_urls.app) as target:
        login(target, a.slug, member_email)
        login(admin, a.slug, a.admin_email)
        assert admin.patch(f"{API}/users/{member}", json={"is_active": False}, headers=csrf(admin)).status_code == 200
        assert target.get(f"{API}/auth/me").status_code == 401
    with owner_engine.connect() as conn:
        revoked = conn.execute(select(sessions.c.revoked_at).where(sessions.c.user_id == member)).scalars().all()
    assert revoked and all(r is not None for r in revoked)


def test_tenant_rename(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    a = create_tenant_with_admin(owner_engine)
    with client_for(database_urls.app) as admin:
        login(admin, a.slug, a.admin_email)
        assert admin.patch(f"{API}/tenant", json={"name": "Renamed"}, headers=csrf(admin)).status_code == 200
        assert admin.get(f"{API}/tenant").json()["name"] == "Renamed"


def test_concurrent_mutual_demotion_keeps_one_admin(owner_engine: Engine, app_engine: Engine) -> None:
    a = create_tenant_with_admin(owner_engine)
    second_admin, _ = create_user(owner_engine, a.tenant_id, role=Role.ADMIN)
    session = UUID(int=0)
    ctx_a = AuthContext(user_id=a.admin_id, tenant_id=a.tenant_id, role=Role.ADMIN, session_id=session)
    ctx_b = AuthContext(user_id=second_admin, tenant_id=a.tenant_id, role=Role.ADMIN, session_id=session)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def demote(ctx: AuthContext, target: UUID) -> None:
        barrier.wait()
        try:
            user_service.update_user(app_engine, ctx, target, role=Role.MEMBER, is_active=None)
            outcomes.append("ok")
        except Conflict:
            outcomes.append("conflict")

    threads = [
        threading.Thread(target=demote, args=(ctx_a, second_admin)),
        threading.Thread(target=demote, args=(ctx_b, a.admin_id)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["conflict", "ok"]
    with owner_engine.connect() as conn:
        admins = conn.execute(
            select(users.c.id).where(users.c.tenant_id == a.tenant_id, users.c.role == "admin", users.c.is_active)
        ).scalars().all()
    assert len(admins) == 1
```

`backend/tests/security/test_privilege_escalation.py`:
```python
"""T10 (role escalation and privilege bypass)."""

import pytest
from sqlalchemy import Engine

from app.authz import Role
from tests.api import API, client_for, csrf, login
from tests.conftest import DatabaseUrls
from tests.factories import create_tenant_with_admin, create_user

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("role", [Role.MEMBER, Role.MANAGER])
def test_non_admins_cannot_manage_users(owner_engine: Engine, database_urls: DatabaseUrls, role: Role) -> None:
    a = create_tenant_with_admin(owner_engine)
    user_id, email = create_user(owner_engine, a.tenant_id, role=role)
    with client_for(database_urls.app) as client:
        login(client, a.slug, email)
        h = csrf(client)
        assert client.get(f"{API}/users").status_code == 403
        new_user = {"email": "x@y.test", "password": "another-long-password", "role": "admin"}
        assert client.post(f"{API}/users", json=new_user, headers=h).status_code == 403
        assert client.patch(f"{API}/users/{user_id}", json={"role": "admin"}, headers=h).status_code == 403
        assert client.patch(f"{API}/tenant", json={"name": "Mine now"}, headers=h).status_code == 403


def test_admin_cannot_change_own_role_or_status(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    a = create_tenant_with_admin(owner_engine)
    with client_for(database_urls.app) as admin:
        login(admin, a.slug, a.admin_email)
        h = csrf(admin)
        assert admin.patch(f"{API}/users/{a.admin_id}", json={"role": "member"}, headers=h).status_code == 403
        assert admin.patch(f"{API}/users/{a.admin_id}", json={"is_active": False}, headers=h).status_code == 403


def test_demotion_takes_effect_on_the_next_request(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    a = create_tenant_with_admin(owner_engine)
    other_admin, other_email = create_user(owner_engine, a.tenant_id, role=Role.ADMIN)
    with client_for(database_urls.app) as first, client_for(database_urls.app) as second:
        login(first, a.slug, a.admin_email)
        login(second, a.slug, other_email)
        assert second.get(f"{API}/users").status_code == 200
        r = first.patch(f"{API}/users/{other_admin}", json={"role": "member"}, headers=csrf(first))
        assert r.status_code == 200
        assert second.get(f"{API}/users").status_code == 403
```

`backend/tests/security/test_forged_identifiers.py`:
```python
"""T4 / brief test 4 (manipulating client-supplied tenant IDs does not bypass isolation)."""

import pytest
from sqlalchemy import Engine

from tests.api import API, client_for, csrf, login
from tests.conftest import DatabaseUrls
from tests.factories import create_tenant_with_admin

pytestmark = pytest.mark.integration


def test_tenant_id_in_request_bodies_is_rejected(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    a = create_tenant_with_admin(owner_engine)
    b = create_tenant_with_admin(owner_engine)
    forged = str(b.tenant_id)
    with client_for(database_urls.app) as client:
        r = client.post(
            f"{API}/auth/login",
            json={"tenant_slug": a.slug, "email": a.admin_email, "password": "x" * 12, "tenant_id": forged},
        )
        assert r.status_code == 422
        login(client, a.slug, a.admin_email)
        h = csrf(client)
        new_user = {"email": "f@x.test", "password": "another-long-password", "role": "member", "tenant_id": forged}
        assert client.post(f"{API}/users", json=new_user, headers=h).status_code == 422
        assert client.patch(f"{API}/tenant", json={"name": "N", "id": forged}, headers=h).status_code == 422


def test_tenant_headers_and_query_params_do_not_change_scope(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    a = create_tenant_with_admin(owner_engine)
    b = create_tenant_with_admin(owner_engine)
    forged_headers = {"X-Tenant-ID": str(b.tenant_id), "X-User-ID": str(b.admin_id)}
    forged_params = {"tenant_id": str(b.tenant_id)}
    with client_for(database_urls.app) as client:
        login(client, a.slug, a.admin_email)
        tenant = client.get(f"{API}/tenant", headers=forged_headers, params=forged_params).json()
        user_ids = {u["id"] for u in client.get(f"{API}/users", headers=forged_headers, params=forged_params).json()}
        me = client.get(f"{API}/auth/me", headers=forged_headers).json()
    assert tenant["id"] == str(a.tenant_id)
    assert user_ids == {str(a.admin_id)}
    assert me["user"]["id"] == str(a.admin_id)
```

`backend/tests/security/test_tenant_isolation.py`:
```python
"""T1 / brief test 1 (tenant A cannot reach tenant B), user-management surface.
Phase 3 extends this module to documents and retrieval."""

import pytest
from sqlalchemy import Engine, select

from app.db.schema import users
from tests.api import API, client_for, csrf, login
from tests.conftest import DatabaseUrls
from tests.factories import create_tenant_with_admin, create_user

pytestmark = pytest.mark.integration


def test_admin_cannot_modify_users_in_another_tenant(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    a = create_tenant_with_admin(owner_engine)
    b = create_tenant_with_admin(owner_engine)
    victim, _ = create_user(owner_engine, b.tenant_id)
    with client_for(database_urls.app) as client:
        login(client, a.slug, a.admin_email)
        r = client.patch(f"{API}/users/{victim}", json={"role": "admin"}, headers=csrf(client))
    assert (r.status_code, r.json()["error"]["code"]) == (404, "not_found")
    with owner_engine.connect() as conn:
        assert conn.execute(select(users.c.role).where(users.c.id == victim)).scalar_one() == "member"


def test_session_from_one_tenant_never_sees_another(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    a = create_tenant_with_admin(owner_engine, name="Alpha")
    create_tenant_with_admin(owner_engine, name="Beta")
    with client_for(database_urls.app) as client:
        login(client, a.slug, a.admin_email)
        assert client.get(f"{API}/tenant").json()["name"] == "Alpha"
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/integration/test_users_api.py tests/security -q`
Expected: new tests FAIL (404 on `/tenant` and `/users`; `No module named 'app.users'`); Task 6 security tests still PASS.

- [ ] **Step 4: Implement**

`backend/app/users/service.py`:
```python
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, Row, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.auth.passwords import hash_password
from app.authz import Action, AuthContext, Role, authorize
from app.db.schema import sessions, tenants, users
from app.db.tenancy import tenant_transaction
from app.errors import Conflict, Forbidden, NotFound


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: UUID
    email: str
    role: Role
    is_active: bool
    created_at: datetime


_COLUMNS = (users.c.id, users.c.email, users.c.role, users.c.is_active, users.c.created_at)


def _record(row: Row[Any]) -> UserRecord:
    return UserRecord(
        id=row.id, email=row.email, role=Role(row.role), is_active=row.is_active, created_at=row.created_at
    )


def list_users(engine: Engine, ctx: AuthContext) -> list[UserRecord]:
    authorize(ctx, Action.USER_MANAGE)
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        rows = conn.execute(
            select(*_COLUMNS)
            .where(users.c.tenant_id == ctx.tenant_id)
            .order_by(users.c.created_at, users.c.email)
        ).all()
    return [_record(r) for r in rows]


def create_user(engine: Engine, ctx: AuthContext, *, email: str, password: str, role: Role) -> UserRecord:
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
    except IntegrityError as exc:
        raise Conflict("A user with this email already exists.") from exc
    return _record(row)


def update_user(
    engine: Engine, ctx: AuthContext, user_id: UUID, *, role: Role | None, is_active: bool | None
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
        # Serialise membership changes per tenant so concurrent demotions cannot
        # leave a tenant without an admin.
        conn.execute(select(tenants.c.id).where(tenants.c.id == ctx.tenant_id).with_for_update())
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
            .where(users.c.tenant_id == ctx.tenant_id, users.c.role == Role.ADMIN.value, users.c.is_active)
        ).scalar_one()
        if active_admins == 0:
            raise Conflict("A tenant must keep at least one active admin.")
        conn.execute(
            update(tenants)
            .where(tenants.c.id == ctx.tenant_id)
            .values(policy_version=tenants.c.policy_version + 1)
        )
        if is_active is False:
            conn.execute(
                update(sessions)
                .where(sessions.c.user_id == user_id, sessions.c.revoked_at.is_(None))
                .values(revoked_at=func.now())
            )
    return _record(row)
```

`backend/app/users/router.py`:
```python
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.auth.deps import CurrentUser, EngineDep, require_csrf
from app.auth.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH
from app.authz import Role
from app.users import service
from app.users.service import UserRecord

router = APIRouter(prefix="/users", tags=["users"])


class UserOut(BaseModel):
    id: UUID
    email: str
    role: Role
    is_active: bool
    created_at: datetime


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    role: Role


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _require_a_change(self) -> "UserUpdate":
        if self.role is None and self.is_active is None:
            raise ValueError("Provide role and/or is_active.")
        return self


def _out(record: UserRecord) -> UserOut:
    return UserOut(
        id=record.id, email=record.email, role=record.role, is_active=record.is_active, created_at=record.created_at
    )


@router.get("")
def list_users(ctx: CurrentUser, engine: EngineDep) -> list[UserOut]:
    return [_out(r) for r in service.list_users(engine, ctx)]


@router.post("", status_code=201, dependencies=[Depends(require_csrf)])
def create_user(body: UserCreate, ctx: CurrentUser, engine: EngineDep) -> UserOut:
    return _out(service.create_user(engine, ctx, email=body.email, password=body.password, role=body.role))


@router.patch("/{user_id}", dependencies=[Depends(require_csrf)])
def update_user(user_id: UUID, body: UserUpdate, ctx: CurrentUser, engine: EngineDep) -> UserOut:
    return _out(service.update_user(engine, ctx, user_id, role=body.role, is_active=body.is_active))
```

`backend/app/tenants/router.py`:
```python
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
            update(tenants).where(tenants.c.id == ctx.tenant_id).values(name=body.name).returning(*_COLUMNS)
        ).one()
    return TenantOut(id=row.id, slug=row.slug, name=row.name)
```

In `backend/app/main.py` import `from app.tenants import router as tenants_router` and `from app.users import router as users_router`, then include both under `API_PREFIX` after the auth router.

- [ ] **Step 5: Run full suite + lint + types** — Expected: PASS, clean. `test_concurrent_mutual_demotion_keeps_one_admin` must pass reliably; run it 5 times: `for i in 1 2 3 4 5; do uv run pytest -q tests/integration/test_users_api.py::test_concurrent_mutual_demotion_keeps_one_admin || break; done`.

- [ ] **Step 6: README (tenant/users endpoints), devlog, commit, push**

```bash
git add -A
git commit -m "feat(api): tenant and user management with escalation and isolation guards"
git push origin main
```

---

### Task 8: Compose, end-to-end smoke, documentation, review

**Files:**
- Modify: `docker-compose.yml`, `.github/workflows/ci.yml`, `README.md`, `docs/devlog.md`, `docs/decisions.md`, `docs/superpowers/specs/2026-10-15-veyl-design.md` (§5.1, §7, §11)

- [ ] **Step 1: Compose** — in the `api` service add:

```yaml
    command:
      - uvicorn
      - app.main:create_app
      - --factory
      - --host
      - 0.0.0.0
      - --port
      - "8000"
      - --proxy-headers
      - --forwarded-allow-ips
      - "*"
```
(Trusting `X-Forwarded-For` from any peer is acceptable only because the API port binds to 127.0.0.1 and the Compose network; recorded in spec §11.)

- [ ] **Step 2: Local end-to-end check**

```bash
docker compose down -v && docker compose up -d --build
docker compose run --rm -e VEYL_ADMIN_PASSWORD=local-demo-password migrate \
  python -m app.cli provision-tenant --slug demo --name "Demo" --admin-email admin@demo.test
curl -fsS -c /tmp/veyl.jar -H 'Content-Type: application/json' \
  -d '{"tenant_slug":"demo","email":"admin@demo.test","password":"local-demo-password"}' \
  http://127.0.0.1:3000/api/v1/auth/login
curl -fsS -b /tmp/veyl.jar http://127.0.0.1:3000/api/v1/auth/me
docker compose down
```
Expected: both curls print the admin's `MeResponse` (login through the Next.js proxy sets cookies; `/me` resolves them).

- [ ] **Step 3: CI smoke** — in `.github/workflows/ci.yml` `compose-smoke`, after "Web proxies API" add:

```yaml
      - name: Provision a tenant
        run: >
          docker compose run --rm -e VEYL_ADMIN_PASSWORD=smoke-test-password migrate
          python -m app.cli provision-tenant --slug smoke --name "Smoke Test" --admin-email admin@smoke.test
      - name: Log in through the web proxy
        run: |
          curl -fsS -c jar.txt -H 'Content-Type: application/json' \
            -d '{"tenant_slug":"smoke","email":"admin@smoke.test","password":"smoke-test-password"}' \
            http://127.0.0.1:3000/api/v1/auth/login
          curl -fsS -b jar.txt http://127.0.0.1:3000/api/v1/auth/me
```

- [ ] **Step 4: Docs**
  - `docs/decisions.md`: D-015 owner policies + `app_current_tenant()`; D-016 SQLAlchemy Core + hand-written SQL migrations; D-017 login exempt from CSRF token (JSON-only, no CORS, SameSite=Lax); D-018 operator CLI provisioning, no tenant-creation API; D-019 auth audit events land in Phase 4; D-020 trust `X-Forwarded-For` behind the local proxy.
  - Spec §5.1: replace the `current_setting(...)::uuid` predicate with `app_current_tenant()` and describe the two policies per table. §7: note login is CSRF-exempt and why. §11: add the forwarded-headers limitation.
  - README: Phase 2 row "Done"; Phase 3 "Next"; endpoint table (auth, tenant, users); provisioning; security model summary (sessions, CSRF, lockout, RLS, policy).
  - Devlog: Phase 2 summary; carry over the still-deferred minors from the Phase 0 review.

- [ ] **Step 5: Commit, push, watch CI**

```bash
git add -A
git commit -m "docs: close out Phase 2; compose proxy headers and auth smoke test"
git push origin main
gh run watch --exit-status
```
Expected: backend, frontend, compose-smoke all green.

- [ ] **Step 6: Final whole-phase review** per the executing skill (fresh reviewer, Review Focus above), fix Critical/Important with RED->GREEN tests, record minors in the devlog.
