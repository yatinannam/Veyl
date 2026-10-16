# Phase 3 — Documents, Ingestion, Embeddings, Permission-Filtered Retrieval

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Managers and admins upload PDF/TXT/Markdown documents with access-control metadata; a worker extracts, chunks, and embeds them locally; and a retrieval service returns only chunks the caller may read — enforced inside the pgvector query, re-checked in Python, and never leaked through a cache.

**Architecture:** Migration 0003 adds `documents`, `document_chunks` (pgvector `vector(384)`, denormalised ACL), `ingestion_jobs`, and `retrieval_cache`, all under forced RLS, plus a `SECURITY DEFINER` job-claim function. `app.authz.sql` expresses the read rule as SQLAlchemy predicates, proven equal to the Python rule over an exhaustive ACL universe. Uploads are validated (size streamed, extension + content sniff), stored under server-generated keys, and queued. The worker runs extraction/chunking/embedding outside the transaction and persists chunks under `FOR SHARE` on the document, so permission changes cannot interleave. Retrieval reads `policy_version`, the cache, and the vector search in one tenant transaction with pgvector iterative scans.

**Tech Stack:** FastAPI (+ python-multipart), SQLAlchemy 2.1 Core, pgvector 0.8.7 (+ `pgvector` Python package), fastembed (`BAAI/bge-small-en-v1.5`, ONNX), pypdf, pytest + testcontainers + Hypothesis.

**Spec:** `docs/superpowers/specs/2026-10-15-veyl-design.md` (§4.3 chunk rule, §5.2-5.4 schema and permission changes, §6.1 ingestion, §6.2 retrieval, §6.3 cache, §7 documents API, §9 T1, T2, T4, T6, T7, T11, T14)

**Code blocks:** a fence tagged `file=PATH` is the complete content of that file (relative to the repo root).

## Global Constraints

- Spec is binding; deviations go in `docs/decisions.md`.
- No emojis anywhere (enforced by `tests/unit/test_no_emojis.py`).
- $0, no card. Embeddings run locally; the model is baked into the Docker image at build time.
- Every commit updates `README.md` and `docs/devlog.md` and is pushed to `origin main`.
- Tenant and user identity come only from `AuthContext`. No service function takes a tenant id from a request.
- Every new table: ENABLE + FORCE RLS, `*_tenant_isolation` (veyl_app) and `*_owner_all` (veyl_owner) policies, column-level grants only.
- Cross-tenant or unreadable documents return 404; readable but not manageable returns 403.
- There is no fallback retrieval path. Embedding, database, or authorization-check failure fails the request.
- Backend checks before every commit: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy` (in `backend/`).

## Review Focus

1. **A permission change racing an in-flight ingestion** (worker read the old ACL, admin narrows access, worker writes chunks) — expect chunks to carry the new ACL. Test: Task 6 `test_permission_change_during_ingestion_reaches_new_chunks`.
2. **Upload bodies far larger than the limit, sent chunked without Content-Length** — expect 413 before the whole body is buffered, nothing stored. Test: Task 4 `test_streamed_body_over_limit_is_rejected_without_content_length`.
3. **A small tenant whose relevant chunk sits behind thousands of closer chunks from another tenant** — expect it to be found (RLS filters after the HNSW scan; iterative scans keep recall). Test: Task 7 `test_small_tenant_keeps_recall_next_to_a_large_tenant`.
4. **A forged or stale retrieval-cache row** (written for one user, or before a permission change) — expect it never to surface an unauthorized chunk. Tests: Task 7 `test_forged_cache_entry_is_filtered_on_hit`, `test_permission_change_invalidates_cached_results`.
5. **Hostile filenames** (`../../etc/passwd.txt`, Windows separators, control characters) — expect metadata-only use, storage under `tenant/document` keys. Test: Task 5 `test_hostile_filename_is_metadata_only`.

## Spec refinements (record in `docs/decisions.md` in Task 8)

- `allowed_roles` is `text[]` with `CHECK (allowed_roles <@ ARRAY['member','manager','admin'])` rather than `user_role[]`: same rejection of unknown roles, and no enum-array adapter fragility in psycopg/SQLAlchemy.
- Phase 3 exposes no query endpoint; retrieval is a service tested directly. `POST /query` arrives with the LLM in Phase 4.
- The uploader stays in `allowed_user_ids` on every permission change (spec: uploaders are added automatically).
- Retrieval sets `hnsw.iterative_scan = relaxed_order` per transaction and sorts results by score in Python (relaxed order may return slightly out-of-order rows).
- Membership changes lock the tenant row `FOR NO KEY UPDATE` (Phase 2 review minor 2), so they never block document or chunk inserts.

---

### Task 1: Schema migration 0003 and job-claim function

**Files:**
- Create: `backend/migrations/versions/0003_documents_chunks_jobs_cache.py`, `backend/tests/integration/test_documents_schema.py`, `backend/tests/integration/test_ingestion_claim.py`
- Modify (full replacement): `backend/app/config.py`, `backend/app/db/schema.py`, `backend/app/db/tenancy.py`, `backend/app/users/service.py`, `backend/tests/factories.py`
- Modify: `backend/tests/integration/test_auth_functions.py` (definer allowlist), `backend/pyproject.toml` (deps), `README.md`, `docs/devlog.md`

**Interfaces:**
- Produces:
  - Settings: `upload_dir`, `max_upload_mb`, `max_pdf_pages`, `embedding_model`, `embedding_cache_dir`, `retrieval_top_k`, `retrieval_min_score`, `retrieval_cache_ttl_seconds`, `worker_poll_seconds`.
  - Core tables `documents`, `document_chunks`, `ingestion_jobs`, `retrieval_cache`; `EMBEDDING_DIM = 384`.
  - `app.db.tenancy.bump_policy_version(conn, tenant_id) -> None`.
  - `app.users.service.lock_tenant_membership(conn, tenant_id) -> None` (FOR NO KEY UPDATE).
  - SQL `ingestion_claim_job(p_stale_after interval) RETURNS TABLE (job_id uuid, tenant_id uuid, document_id uuid, attempts integer)`.
  - Factories: `create_document`, `add_chunks`, `unit_vector`, `create_job`, `drain_jobs`.

- [ ] **Step 1: Dependencies**

Run (in `backend/`): `uv add pgvector fastembed pypdf python-multipart`

Append to `backend/pyproject.toml`:
```toml
[[tool.mypy.overrides]]
module = ["fastembed.*", "pgvector.*"]
ignore_missing_imports = true
```

- [ ] **Step 2: Settings, schema, tenancy, users service, factories** (full files)

```python file=backend/app/config.py
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
    login_max_concurrent_hashes: int = Field(default=4, ge=1)

    upload_dir: str = "/data/uploads"
    max_upload_mb: int = Field(default=10, ge=1, le=100)
    max_pdf_pages: int = Field(default=200, ge=1)
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: str | None = None
    retrieval_top_k: int = Field(default=6, ge=1, le=50)
    retrieval_min_score: float = Field(default=0.0, ge=-1.0, le=1.0)
    retrieval_cache_ttl_seconds: int = Field(default=600, ge=0)
    worker_poll_seconds: float = Field(default=2.0, gt=0)

    @property
    def cookie_secure(self) -> bool:
        return self.environment == "production"
```

```python file=backend/app/db/schema.py
"""SQLAlchemy Core definitions mirroring the hand-written migrations.
`tests/integration/test_schema.py` fails if the two drift apart."""

from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, ENUM, UUID

from app.authz.roles import Classification, Role

EMBEDDING_DIM = 384

metadata = MetaData()

user_role = ENUM(*[r.value for r in Role], name="user_role", create_type=False)
classification = ENUM(
    *[c.value for c in Classification], name="classification", create_type=False
)
doc_status = ENUM("pending", "processing", "ready", "failed", name="doc_status", create_type=False)
job_status = ENUM("queued", "running", "done", "failed", name="job_status", create_type=False)


def _id() -> Column[Any]:
    return Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _created_at() -> Column[Any]:
    return Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now())


def _updated_at() -> Column[Any]:
    return Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now())


def _acl_columns() -> list[Column[Any]]:
    return [
        Column("classification", classification, nullable=False),
        Column("allowed_roles", ARRAY(Text), nullable=False, server_default=text("'{}'")),
        Column(
            "allowed_user_ids",
            ARRAY(UUID(as_uuid=True)),
            nullable=False,
            server_default=text("'{}'"),
        ),
    ]


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
    Column(
        "tenant_id",
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    ),
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

documents = Table(
    "documents",
    metadata,
    _id(),
    Column(
        "tenant_id",
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("uploaded_by", UUID(as_uuid=True), nullable=False),
    Column("title", Text, nullable=False),
    Column("original_filename", Text, nullable=False),
    Column("mime_type", Text, nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("sha256", Text, nullable=False),
    Column("storage_key", Text, nullable=False),
    *_acl_columns(),
    Column("acl_version", Integer, nullable=False, server_default=text("1")),
    Column("status", doc_status, nullable=False, server_default=text("'pending'")),
    Column("error", Text, nullable=True),
    _created_at(),
    _updated_at(),
    Column("deleted_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("tenant_id", "id"),
    ForeignKeyConstraint(["tenant_id", "uploaded_by"], ["users.tenant_id", "users.id"]),
)

document_chunks = Table(
    "document_chunks",
    metadata,
    _id(),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("document_id", UUID(as_uuid=True), nullable=False),
    Column("chunk_index", Integer, nullable=False),
    Column("content", Text, nullable=False),
    Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
    *_acl_columns(),
    Column("acl_version", Integer, nullable=False),
    _created_at(),
    UniqueConstraint("document_id", "chunk_index"),
    ForeignKeyConstraint(
        ["tenant_id", "document_id"],
        ["documents.tenant_id", "documents.id"],
        ondelete="CASCADE",
    ),
)

ingestion_jobs = Table(
    "ingestion_jobs",
    metadata,
    _id(),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("document_id", UUID(as_uuid=True), nullable=False),
    Column("status", job_status, nullable=False, server_default=text("'queued'")),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    Column("run_after", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("locked_at", DateTime(timezone=True), nullable=True),
    Column("last_error", Text, nullable=True),
    _created_at(),
    _updated_at(),
    ForeignKeyConstraint(
        ["tenant_id", "document_id"],
        ["documents.tenant_id", "documents.id"],
        ondelete="CASCADE",
    ),
)

retrieval_cache = Table(
    "retrieval_cache",
    metadata,
    Column("cache_key", LargeBinary, primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("policy_version", BigInteger, nullable=False),
    Column("chunk_ids", ARRAY(UUID(as_uuid=True)), nullable=False),
    Column("scores", ARRAY(Float), nullable=False),
    _created_at(),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["tenant_id", "user_id"], ["users.tenant_id", "users.id"], ondelete="CASCADE"
    ),
)
```

```python file=backend/app/db/tenancy.py
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import Connection, Engine, text, update

from app.db.schema import tenants


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


def bump_policy_version(conn: Connection, tenant_id: UUID) -> None:
    """Invalidate every cached retrieval result for the tenant (cache keys include it)."""
    conn.execute(
        update(tenants)
        .where(tenants.c.id == tenant_id)
        .values(policy_version=tenants.c.policy_version + 1)
    )
```

```python file=backend/app/users/service.py
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, func, insert, select, update
from sqlalchemy.exc import IntegrityError

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
            conn.execute(
                update(sessions)
                .where(sessions.c.user_id == user_id, sessions.c.revoked_at.is_(None))
                .values(revoked_at=func.now())
            )
    return _record(row)
```

```python file=backend/tests/factories.py
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from functools import cache
from uuid import UUID

from sqlalchemy import Engine, func, insert, select, update

from app.auth.passwords import hash_password
from app.auth.tokens import hash_token, new_session_token
from app.authz import Classification, Role
from app.db.schema import (
    EMBEDDING_DIM,
    document_chunks,
    documents,
    ingestion_jobs,
    sessions,
    tenants,
    users,
)

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


def create_document(
    owner_engine: Engine,
    tenant_id: UUID,
    uploaded_by: UUID,
    *,
    classification: Classification = Classification.INTERNAL,
    allowed_roles: Iterable[Role] = (),
    allowed_user_ids: Iterable[UUID] = (),
    status: str = "ready",
    title: str = "Doc",
    acl_version: int = 1,
) -> UUID:
    document_id = uuid.uuid4()
    with owner_engine.begin() as conn:
        conn.execute(
            insert(documents).values(
                id=document_id,
                tenant_id=tenant_id,
                uploaded_by=uploaded_by,
                title=title,
                original_filename="doc.txt",
                mime_type="text/plain",
                size_bytes=1,
                sha256="0" * 64,
                storage_key=f"{tenant_id}/{document_id}",
                classification=classification.value,
                allowed_roles=sorted(r.value for r in allowed_roles),
                allowed_user_ids=sorted(allowed_user_ids, key=str),
                status=status,
                acl_version=acl_version,
            )
        )
    return document_id


def unit_vector(index: int) -> list[float]:
    vector = [0.0] * EMBEDDING_DIM
    vector[index % EMBEDDING_DIM] = 1.0
    return vector


def add_chunks(
    owner_engine: Engine,
    document_id: UUID,
    contents: Sequence[str],
    vectors: Sequence[Sequence[float]],
) -> list[UUID]:
    """Insert chunks that copy the document's current ACL, as the worker does."""
    ids: list[UUID] = []
    with owner_engine.begin() as conn:
        doc = conn.execute(
            select(
                documents.c.tenant_id,
                documents.c.classification,
                documents.c.allowed_roles,
                documents.c.allowed_user_ids,
                documents.c.acl_version,
            ).where(documents.c.id == document_id)
        ).one()
        for index, (content, vector) in enumerate(zip(contents, vectors, strict=True)):
            ids.append(
                conn.execute(
                    insert(document_chunks)
                    .values(
                        tenant_id=doc.tenant_id,
                        document_id=document_id,
                        chunk_index=index,
                        content=content,
                        embedding=list(vector),
                        classification=doc.classification,
                        allowed_roles=doc.allowed_roles,
                        allowed_user_ids=doc.allowed_user_ids,
                        acl_version=doc.acl_version,
                    )
                    .returning(document_chunks.c.id)
                ).scalar_one()
            )
    return ids


def create_job(
    owner_engine: Engine,
    tenant_id: UUID,
    document_id: UUID,
    *,
    status: str = "queued",
    run_after: timedelta = timedelta(0),
    locked_ago: timedelta | None = None,
) -> UUID:
    with owner_engine.begin() as conn:
        return conn.execute(  # type: ignore[no-any-return]
            insert(ingestion_jobs)
            .values(
                tenant_id=tenant_id,
                document_id=document_id,
                status=status,
                run_after=func.now() + run_after,
                locked_at=(func.now() - locked_ago) if locked_ago is not None else None,
            )
            .returning(ingestion_jobs.c.id)
        ).scalar_one()


def drain_jobs(owner_engine: Engine) -> None:
    """The job queue is global; tests that claim jobs start from an empty queue."""
    with owner_engine.begin() as conn:
        conn.execute(
            update(ingestion_jobs)
            .where(ingestion_jobs.c.status.in_(["queued", "running"]))
            .values(status="done")
        )
```

- [ ] **Step 3: Write failing tests**

In `backend/tests/integration/test_auth_functions.py` replace
`DEFINER_FUNCTIONS_FOR_APP = {"auth_resolve_login", "auth_resolve_session"}` with
`DEFINER_FUNCTIONS_FOR_APP = {"auth_resolve_login", "auth_resolve_session", "ingestion_claim_job"}`.

```python file=backend/tests/integration/test_documents_schema.py
import uuid

import pytest
from sqlalchemy import Engine, insert, select, text
from sqlalchemy.exc import IntegrityError

from app.authz import Role
from app.db.schema import document_chunks, documents
from app.db.tenancy import tenant_transaction
from app.users.service import lock_tenant_membership
from tests.factories import create_document, create_tenant, create_user, unit_vector

pytestmark = pytest.mark.integration


def _document_values(tenant_id: uuid.UUID, user_id: uuid.UUID, **overrides: object) -> dict[str, object]:
    document_id = uuid.uuid4()
    values: dict[str, object] = {
        "id": document_id,
        "tenant_id": tenant_id,
        "uploaded_by": user_id,
        "title": "T",
        "original_filename": "t.txt",
        "mime_type": "text/plain",
        "size_bytes": 1,
        "sha256": "0" * 64,
        "storage_key": f"{tenant_id}/{document_id}",
        "classification": "internal",
    }
    values.update(overrides)
    return values


def test_pgvector_supports_iterative_index_scans(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        version = conn.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one()
    assert tuple(int(part) for part in version.split(".")[:2]) >= (0, 8)


def test_unknown_roles_are_rejected(owner_engine: Engine) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    user_id, _ = create_user(owner_engine, tenant_id, role=Role.ADMIN)
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        conn.execute(
            insert(documents).values(
                **_document_values(tenant_id, user_id, allowed_roles=["superuser"])
            )
        )


def test_chunk_cannot_reference_a_document_in_another_tenant(owner_engine: Engine) -> None:
    tenant_a, _ = create_tenant(owner_engine)
    tenant_b, _ = create_tenant(owner_engine)
    user_a, _ = create_user(owner_engine, tenant_a, role=Role.ADMIN)
    document_a = create_document(owner_engine, tenant_a, user_a)
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        conn.execute(
            insert(document_chunks).values(
                tenant_id=tenant_b,
                document_id=document_a,
                chunk_index=0,
                content="x",
                embedding=unit_vector(0),
                classification="internal",
                allowed_roles=[],
                allowed_user_ids=[],
                acl_version=1,
            )
        )


def test_document_uploader_must_belong_to_the_same_tenant(owner_engine: Engine) -> None:
    tenant_a, _ = create_tenant(owner_engine)
    tenant_b, _ = create_tenant(owner_engine)
    user_b, _ = create_user(owner_engine, tenant_b, role=Role.ADMIN)
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        conn.execute(insert(documents).values(**_document_values(tenant_a, user_b)))


def test_membership_lock_does_not_block_document_inserts(
    owner_engine: Engine, app_engine: Engine
) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    user_id, _ = create_user(owner_engine, tenant_id, role=Role.ADMIN)
    with tenant_transaction(app_engine, tenant_id) as locked:
        lock_tenant_membership(locked, tenant_id)
        with app_engine.connect() as other, other.begin():
            other.execute(text("SET LOCAL lock_timeout = '2s'"))
            other.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)}
            )
            other.execute(insert(documents).values(**_document_values(tenant_id, user_id)))
    with owner_engine.connect() as conn:
        count = conn.execute(
            select(documents.c.id).where(documents.c.tenant_id == tenant_id)
        ).all()
    assert len(count) == 1
```

```python file=backend/tests/integration/test_ingestion_claim.py
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text

from app.authz import Role
from tests.factories import create_document, create_job, create_tenant, create_user, drain_jobs

pytestmark = pytest.mark.integration

STALE = timedelta(minutes=10)


def _claim(conn: Connection) -> Any:
    return conn.execute(
        text("SELECT * FROM ingestion_claim_job(:stale)"), {"stale": STALE}
    ).one_or_none()


def _pending_document(owner_engine: Engine) -> tuple[Any, Any]:
    tenant_id, _ = create_tenant(owner_engine)
    user_id, _ = create_user(owner_engine, tenant_id, role=Role.ADMIN)
    return tenant_id, create_document(owner_engine, tenant_id, user_id, status="pending")


def test_claim_returns_a_queued_job_exactly_once(owner_engine: Engine, app_engine: Engine) -> None:
    drain_jobs(owner_engine)
    tenant_id, document_id = _pending_document(owner_engine)
    job_id = create_job(owner_engine, tenant_id, document_id)
    with app_engine.begin() as conn:
        first = _claim(conn)
    with app_engine.begin() as conn:
        second = _claim(conn)
    assert first is not None
    assert (first.job_id, first.tenant_id, first.document_id, first.attempts) == (
        job_id,
        tenant_id,
        document_id,
        1,
    )
    assert second is None


def test_claim_skips_jobs_scheduled_for_later(owner_engine: Engine, app_engine: Engine) -> None:
    drain_jobs(owner_engine)
    tenant_id, document_id = _pending_document(owner_engine)
    create_job(owner_engine, tenant_id, document_id, run_after=timedelta(minutes=5))
    with app_engine.begin() as conn:
        assert _claim(conn) is None


def test_claim_reclaims_stale_running_jobs(owner_engine: Engine, app_engine: Engine) -> None:
    drain_jobs(owner_engine)
    tenant_id, document_id = _pending_document(owner_engine)
    job_id = create_job(
        owner_engine, tenant_id, document_id, status="running", locked_ago=timedelta(hours=1)
    )
    with app_engine.begin() as conn:
        claimed = _claim(conn)
    assert claimed is not None
    assert claimed.job_id == job_id


def test_concurrent_claims_take_different_jobs(owner_engine: Engine, app_engine: Engine) -> None:
    drain_jobs(owner_engine)
    tenant_id, document_id = _pending_document(owner_engine)
    other_tenant, other_document = _pending_document(owner_engine)
    create_job(owner_engine, tenant_id, document_id)
    create_job(owner_engine, other_tenant, other_document)
    with app_engine.connect() as first, app_engine.connect() as second:
        with first.begin():
            a = _claim(first)
            with second.begin():
                second.execute(text("SET LOCAL lock_timeout = '2s'"))
                b = _claim(second)
    assert a is not None and b is not None
    assert a.job_id != b.job_id
```

Note: the second document lives in a second tenant; claiming is cross-tenant by design.

- [ ] **Step 4: Run to verify failure**

Run: `uv run pytest tests/integration/test_documents_schema.py tests/integration/test_ingestion_claim.py tests/integration/test_auth_functions.py -q`
Expected: FAIL — `relation "documents" does not exist`, `function ingestion_claim_job(...) does not exist`, definer allowlist mismatch.

- [ ] **Step 5: Migration**

```python file=backend/migrations/versions/0003_documents_chunks_jobs_cache.py
"""Documents, chunks with denormalised ACLs and embeddings, ingestion jobs, retrieval
cache; job-claim function.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-16
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _rls(table: str) -> list[str]:
    """App role sees only the current tenant; the owner (migrations and SECURITY DEFINER
    functions) sees all rows. FORCE makes the policies apply to the owner as well."""
    predicate = "tenant_id = app_current_tenant()"
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {table}_tenant_isolation ON {table} FOR ALL TO veyl_app "
        f"USING ({predicate}) WITH CHECK ({predicate})",
        f"CREATE POLICY {table}_owner_all ON {table} FOR ALL TO veyl_owner "
        "USING (true) WITH CHECK (true)",
    ]


ROLES_CHECK = "allowed_roles <@ ARRAY['member', 'manager', 'admin']::text[]"

UPGRADE = [
    "CREATE TYPE classification AS ENUM ('public', 'internal', 'confidential', 'restricted')",
    "CREATE TYPE doc_status AS ENUM ('pending', 'processing', 'ready', 'failed')",
    "CREATE TYPE job_status AS ENUM ('queued', 'running', 'done', 'failed')",
    f"""
    CREATE TABLE documents (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
        uploaded_by uuid NOT NULL,
        title text NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
        original_filename text NOT NULL CHECK (length(original_filename) BETWEEN 1 AND 255),
        mime_type text NOT NULL,
        size_bytes integer NOT NULL CHECK (size_bytes > 0),
        sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{{64}}$'),
        storage_key text NOT NULL,
        classification classification NOT NULL,
        allowed_roles text[] NOT NULL DEFAULT '{{}}' CHECK ({ROLES_CHECK}),
        allowed_user_ids uuid[] NOT NULL DEFAULT '{{}}',
        acl_version integer NOT NULL DEFAULT 1,
        status doc_status NOT NULL DEFAULT 'pending',
        error text,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        deleted_at timestamptz,
        UNIQUE (tenant_id, id),
        FOREIGN KEY (tenant_id, uploaded_by) REFERENCES users (tenant_id, id)
    )
    """,
    "CREATE INDEX documents_tenant_created_idx ON documents (tenant_id, created_at DESC)",
    f"""
    CREATE TABLE document_chunks (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL,
        document_id uuid NOT NULL,
        chunk_index integer NOT NULL CHECK (chunk_index >= 0),
        content text NOT NULL,
        embedding vector(384) NOT NULL,
        classification classification NOT NULL,
        allowed_roles text[] NOT NULL CHECK ({ROLES_CHECK}),
        allowed_user_ids uuid[] NOT NULL,
        acl_version integer NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (document_id, chunk_index),
        FOREIGN KEY (tenant_id, document_id) REFERENCES documents (tenant_id, id)
            ON DELETE CASCADE
    )
    """,
    "CREATE INDEX document_chunks_embedding_idx ON document_chunks "
    "USING hnsw (embedding vector_cosine_ops)",
    "CREATE INDEX document_chunks_tenant_doc_idx ON document_chunks (tenant_id, document_id)",
    "CREATE INDEX document_chunks_roles_idx ON document_chunks USING gin (allowed_roles)",
    "CREATE INDEX document_chunks_users_idx ON document_chunks USING gin (allowed_user_ids)",
    """
    CREATE TABLE ingestion_jobs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL,
        document_id uuid NOT NULL,
        status job_status NOT NULL DEFAULT 'queued',
        attempts integer NOT NULL DEFAULT 0,
        run_after timestamptz NOT NULL DEFAULT now(),
        locked_at timestamptz,
        last_error text,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY (tenant_id, document_id) REFERENCES documents (tenant_id, id)
            ON DELETE CASCADE
    )
    """,
    "CREATE INDEX ingestion_jobs_queue_idx ON ingestion_jobs (status, run_after)",
    """
    CREATE TABLE retrieval_cache (
        cache_key bytea PRIMARY KEY CHECK (octet_length(cache_key) = 32),
        tenant_id uuid NOT NULL,
        user_id uuid NOT NULL,
        policy_version bigint NOT NULL,
        chunk_ids uuid[] NOT NULL,
        scores double precision[] NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL,
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX retrieval_cache_expires_idx ON retrieval_cache (expires_at)",
    *_rls("documents"),
    *_rls("document_chunks"),
    *_rls("ingestion_jobs"),
    *_rls("retrieval_cache"),
    "GRANT SELECT, INSERT, UPDATE (title, classification, allowed_roles, allowed_user_ids, "
    "acl_version, status, error, updated_at, deleted_at) ON documents TO veyl_app",
    "GRANT SELECT, INSERT, DELETE, UPDATE (classification, allowed_roles, allowed_user_ids, "
    "acl_version) ON document_chunks TO veyl_app",
    "GRANT SELECT, INSERT, UPDATE (status, attempts, run_after, locked_at, last_error, "
    "updated_at) ON ingestion_jobs TO veyl_app",
    "GRANT SELECT, INSERT, DELETE, UPDATE (policy_version, chunk_ids, scores, created_at, "
    "expires_at) ON retrieval_cache TO veyl_app",
    # The worker must find work in any tenant before it knows which tenant to scope to.
    """
    CREATE FUNCTION ingestion_claim_job(p_stale_after interval)
    RETURNS TABLE (job_id uuid, tenant_id uuid, document_id uuid, attempts integer)
    LANGUAGE sql VOLATILE SECURITY DEFINER
    SET search_path = public, pg_temp
    AS $$
        UPDATE ingestion_jobs AS j
        SET status = 'running', locked_at = now(), attempts = j.attempts + 1, updated_at = now()
        WHERE j.id = (
            SELECT q.id FROM ingestion_jobs AS q
            WHERE (q.status = 'queued' AND q.run_after <= now())
               OR (q.status = 'running' AND q.locked_at < now() - p_stale_after)
            ORDER BY q.run_after
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING j.id, j.tenant_id, j.document_id, j.attempts
    $$
    """,
    "REVOKE ALL ON FUNCTION ingestion_claim_job(interval) FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION ingestion_claim_job(interval) TO veyl_app",
]

DOWNGRADE = [
    "DROP FUNCTION ingestion_claim_job(interval)",
    "DROP TABLE retrieval_cache",
    "DROP TABLE ingestion_jobs",
    "DROP TABLE document_chunks",
    "DROP TABLE documents",
    "DROP TYPE job_status",
    "DROP TYPE doc_status",
    "DROP TYPE classification",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
```

- [ ] **Step 6: Run full suite + lint + types** — Expected: all PASS (existing `test_every_table_has_forced_row_level_security` and `test_schema_module_matches_migrated_database` now cover the new tables).

- [ ] **Step 7: README (data model now includes documents/chunks/jobs/cache), devlog, commit, push**

```bash
git add -A
git commit -m "feat(db): documents, chunks with ACL + embeddings, ingestion jobs, retrieval cache"
git push origin main
```

---

### Task 2: SQL read filters, proven equal to the Python policy

**Files:**
- Create: `backend/app/authz/sql.py`, `backend/tests/security/test_filter_equivalence.py`
- Modify: `README.md`, `docs/devlog.md`

**Interfaces:**
- Consumes: `AuthContext`, `can_read_chunk`, `ChunkAcl`, schema tables.
- Produces: `app.authz.sql.readable_classifications(role) -> list[str]`, `document_read_filter(ctx) -> ColumnElement[bool]`, `chunk_read_filter(ctx) -> ColumnElement[bool]` (caller joins `documents` to `document_chunks` with `CHUNK_JOIN`), `CHUNK_JOIN`. Not exported from `app.authz` (import cycle with `app.db.schema`).

- [ ] **Step 1: Write failing tests**

```python file=backend/tests/security/test_filter_equivalence.py
"""T2 (unauthorized retrieval within a tenant): the SQL predicates used inside queries and
the Python policy used for the re-check agree on every ACL in an exhaustive universe:
3 tenants (one unknown) x 3 users x 3 roles as callers, against
4 classifications x 8 role sets x 4 user sets per tenant."""

import uuid
from itertools import combinations
from uuid import UUID

import pytest
from sqlalchemy import Engine, insert, select, update

from app.authz import AuthContext, ChunkAcl, Classification, Role, can_read_chunk
from app.authz.sql import CHUNK_JOIN, chunk_read_filter, document_read_filter
from app.db.schema import document_chunks, documents
from tests.factories import add_chunks, create_document, create_tenant, create_user, unit_vector

pytestmark = pytest.mark.integration

ROLE_SETS = [frozenset(c) for n in range(4) for c in combinations(list(Role), n)]
USER_POOL = (UUID(int=101), UUID(int=102))
USER_SETS = [frozenset(c) for n in range(3) for c in combinations(USER_POOL, n)]
CALLER_IDS = (*USER_POOL, UUID(int=103))

Universe = tuple[list[UUID], dict[UUID, ChunkAcl], dict[UUID, ChunkAcl]]


@pytest.fixture(scope="module")
def universe(owner_engine: Engine) -> Universe:
    tenant_ids: list[UUID] = []
    chunk_acls: dict[UUID, ChunkAcl] = {}
    document_acls: dict[UUID, ChunkAcl] = {}
    for _ in range(2):
        tenant_id, _ = create_tenant(owner_engine)
        admin_id, _ = create_user(owner_engine, tenant_id, role=Role.ADMIN)
        tenant_ids.append(tenant_id)
        holder = create_document(owner_engine, tenant_id, admin_id)
        rows = []
        for classification in Classification:
            for roles in ROLE_SETS:
                for users in USER_SETS:
                    acl = ChunkAcl(tenant_id, roles, users, classification)
                    document_id = create_document(
                        owner_engine,
                        tenant_id,
                        admin_id,
                        classification=classification,
                        allowed_roles=roles,
                        allowed_user_ids=users,
                    )
                    document_acls[document_id] = acl
                    chunk_id = uuid.uuid4()
                    chunk_acls[chunk_id] = acl
                    rows.append(
                        {
                            "id": chunk_id,
                            "tenant_id": tenant_id,
                            "document_id": holder,
                            "chunk_index": len(rows),
                            "content": "x",
                            "embedding": unit_vector(len(rows)),
                            "classification": classification.value,
                            "allowed_roles": sorted(r.value for r in roles),
                            "allowed_user_ids": sorted(users, key=str),
                            "acl_version": 1,
                        }
                    )
        with owner_engine.begin() as conn:
            conn.execute(insert(document_chunks), rows)
    return tenant_ids, chunk_acls, document_acls


def _callers(tenant_ids: list[UUID]) -> list[AuthContext]:
    return [
        AuthContext(user_id=user, tenant_id=tenant, role=role, session_id=UUID(int=0))
        for tenant in (*tenant_ids, UUID(int=999))
        for user in CALLER_IDS
        for role in Role
    ]


def test_chunk_filter_matches_python_policy(owner_engine: Engine, universe: Universe) -> None:
    tenant_ids, chunk_acls, _ = universe
    with owner_engine.connect() as conn:
        for ctx in _callers(tenant_ids):
            from_sql = set(
                conn.execute(
                    select(document_chunks.c.id)
                    .select_from(CHUNK_JOIN)
                    .where(chunk_read_filter(ctx), document_chunks.c.id.in_(list(chunk_acls)))
                ).scalars()
            )
            from_python = {cid for cid, acl in chunk_acls.items() if can_read_chunk(ctx, acl)}
            assert from_sql == from_python, ctx


def test_document_filter_matches_python_policy(owner_engine: Engine, universe: Universe) -> None:
    tenant_ids, _, document_acls = universe
    with owner_engine.connect() as conn:
        for ctx in _callers(tenant_ids):
            from_sql = set(
                conn.execute(
                    select(documents.c.id).where(
                        document_read_filter(ctx), documents.c.id.in_(list(document_acls))
                    )
                ).scalars()
            )
            from_python = {d for d, acl in document_acls.items() if can_read_chunk(ctx, acl)}
            assert from_sql == from_python, ctx


def test_chunks_of_unready_deleted_or_stale_documents_are_invisible(
    owner_engine: Engine,
) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    admin_id, _ = create_user(owner_engine, tenant_id, role=Role.ADMIN)
    admin = AuthContext(user_id=admin_id, tenant_id=tenant_id, role=Role.ADMIN, session_id=UUID(int=0))
    ready = create_document(owner_engine, tenant_id, admin_id)
    processing = create_document(owner_engine, tenant_id, admin_id, status="processing")
    deleted = create_document(owner_engine, tenant_id, admin_id)
    stale = create_document(owner_engine, tenant_id, admin_id)
    expected = add_chunks(owner_engine, ready, ["ok"], [unit_vector(1)])
    for document_id in (processing, deleted, stale):
        add_chunks(owner_engine, document_id, ["hidden"], [unit_vector(2)])
    with owner_engine.begin() as conn:
        conn.execute(update(documents).where(documents.c.id == deleted).values(deleted_at=documents.c.created_at))
        conn.execute(
            update(documents).where(documents.c.id == stale).values(acl_version=documents.c.acl_version + 1)
        )
    with owner_engine.connect() as conn:
        visible = set(
            conn.execute(
                select(document_chunks.c.id)
                .select_from(CHUNK_JOIN)
                .where(chunk_read_filter(admin), document_chunks.c.tenant_id == tenant_id)
            ).scalars()
        )
    assert visible == set(expected)
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/security/test_filter_equivalence.py -q` → FAIL `No module named 'app.authz.sql'`.

- [ ] **Step 3: Implement**

```python file=backend/app/authz/sql.py
"""The read rule (spec section 4.3) as SQL predicates, used inside queries so unauthorized
rows never leave the database. `tests/security/test_filter_equivalence.py` proves these
match `app.authz.can_read_chunk` over an exhaustive ACL universe.

Kept out of `app.authz.__init__` because `app.db.schema` imports `app.authz.roles`."""

from sqlalchemy import ColumnElement, Table, and_, any_, literal, or_, true
from sqlalchemy.dialects.postgresql import UUID

from app.authz.context import AuthContext
from app.authz.roles import Classification, Role, within_clearance
from app.db.schema import document_chunks, documents

CHUNK_JOIN = document_chunks.join(
    documents,
    and_(
        documents.c.tenant_id == document_chunks.c.tenant_id,
        documents.c.id == document_chunks.c.document_id,
    ),
)


def readable_classifications(role: Role) -> list[str]:
    return [c.value for c in Classification if within_clearance(role, c)]


def _grant(ctx: AuthContext, table: Table) -> ColumnElement[bool]:
    if ctx.role is Role.ADMIN:
        return true()
    return or_(
        literal(ctx.role.value) == any_(table.c.allowed_roles),
        literal(ctx.user_id, UUID(as_uuid=True)) == any_(table.c.allowed_user_ids),
    )


def document_read_filter(ctx: AuthContext) -> ColumnElement[bool]:
    return and_(
        documents.c.tenant_id == ctx.tenant_id,
        documents.c.deleted_at.is_(None),
        documents.c.classification.in_(readable_classifications(ctx.role)),
        _grant(ctx, documents),
    )


def chunk_read_filter(ctx: AuthContext) -> ColumnElement[bool]:
    """All five conditions of the chunk read rule. Select from `CHUNK_JOIN`."""
    return and_(
        document_chunks.c.tenant_id == ctx.tenant_id,
        documents.c.status == "ready",
        documents.c.deleted_at.is_(None),
        document_chunks.c.acl_version == documents.c.acl_version,
        document_chunks.c.classification.in_(readable_classifications(ctx.role)),
        _grant(ctx, document_chunks),
    )
```

- [ ] **Step 4: Run full suite + lint + types** — Expected: PASS.

- [ ] **Step 5: README (authorization section: SQL form + equivalence proof), devlog, commit, push**

```bash
git add -A
git commit -m "feat(authz): SQL read filters proven equal to the Python policy"
git push origin main
```

---

### Task 3: Embeddings, extraction, chunking (pure)

**Files:**
- Create: `backend/app/retrieval/__init__.py` (empty), `backend/app/retrieval/embeddings.py`, `backend/app/ingestion/__init__.py` (empty), `backend/app/ingestion/extract.py`, `backend/app/ingestion/chunker.py`, `backend/tests/fakes.py`, `backend/tests/unit/test_chunker.py`, `backend/tests/unit/test_extract.py`, `backend/tests/integration/test_embeddings_model.py`
- Modify: `backend/pyproject.toml` (pytest marker `model`), `README.md`, `docs/devlog.md`

**Interfaces:**
- Produces:
  - `app.retrieval.embeddings.Embedder` (Protocol: `model_name: str`, `embed_documents(texts) -> list[list[float]]`, `embed_query(text) -> list[float]`), `FastEmbedEmbedder(model_name, cache_dir=None)`, `EmbeddingError`.
  - `app.ingestion.extract.extract_text(kind: str, data: bytes, *, max_pdf_pages: int) -> str`, `ExtractionError` (message safe to show users).
  - `app.ingestion.chunker.chunk_text(text, target=800, overlap=120) -> list[str]`.
  - `tests.fakes.FakeEmbedder(fail=False)` (deterministic hashed bag-of-words, counts `query_calls`), `tests.fakes.make_pdf(pages: Sequence[str]) -> bytes`, `tests.fakes.encrypt_pdf(data, password) -> bytes`.

- [ ] **Step 1: pytest marker** — in `[tool.pytest.ini_options]` set
`markers = ["integration: needs Docker (testcontainers Postgres)", "model: downloads and runs the real embedding model"]`.

- [ ] **Step 2: Write failing tests and fakes**

```python file=backend/tests/fakes.py
import hashlib
import io
import math
import re
from collections.abc import Sequence

from pypdf import PdfReader, PdfWriter

from app.db.schema import EMBEDDING_DIM

_TOKEN = re.compile(r"[a-z0-9]+")


class FakeEmbedder:
    """Deterministic hashed bag-of-words. Shared words give real cosine similarity, so
    retrieval tests are meaningful without downloading a model."""

    model_name = "fake-hash-384"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.query_calls = 0

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * EMBEDDING_DIM
        for token in _TOKEN.findall(text.lower()):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            vector[int.from_bytes(digest, "big") % EMBEDDING_DIM] += 1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]

    def _check(self) -> None:
        if self.fail:
            raise RuntimeError("embedding backend unavailable")

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self._check()
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        self._check()
        self.query_calls += 1
        return self._vector(text)


def make_pdf(pages: Sequence[str]) -> bytes:
    """A minimal valid PDF with one line of Helvetica text per page."""
    page_ids = [4 + 2 * i for i in range(len(pages))]
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] "
            f"/Count {len(pages)} >>"
        ).encode(),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for page_id, text in zip(page_ids, pages, strict=True):
        content_id = page_id + 1
        objects[page_id] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode()
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET".encode("latin-1")
        objects[content_id] = (
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for object_id in sorted(objects):
        offsets[object_id] = len(out)
        out += f"{object_id} 0 obj\n".encode() + objects[object_id] + b"\nendobj\n"
    xref = len(out)
    size = max(objects) + 1
    out += f"xref\n0 {size}\n".encode() + b"0000000000 65535 f \n"
    for object_id in range(1, size):
        out += f"{offsets[object_id]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def encrypt_pdf(data: bytes, password: str = "secret") -> bytes:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    writer.encrypt(password)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
```

```python file=backend/tests/unit/test_chunker.py
from itertools import pairwise

from hypothesis import given
from hypothesis import strategies as st

from app.ingestion.chunker import chunk_text

TARGET, OVERLAP = 200, 40


def test_empty_text_has_no_chunks() -> None:
    assert chunk_text("   \n\n  ") == []


def test_short_text_is_one_chunk() -> None:
    assert chunk_text("Hello world.") == ["Hello world."]


def test_long_text_is_split_with_bounded_chunk_size() -> None:
    text = "\n\n".join(f"Paragraph {i} " + "word " * 30 for i in range(20))
    chunks = chunk_text(text, target=TARGET, overlap=OVERLAP)
    assert len(chunks) > 1
    assert all(len(c) <= TARGET + OVERLAP + 1 for c in chunks)


def test_consecutive_chunks_overlap() -> None:
    text = "\n\n".join(f"Sentence number {i} talks about topic {i}." for i in range(40))
    chunks = chunk_text(text, target=TARGET, overlap=OVERLAP)
    for previous, current in pairwise(chunks):
        assert previous.split()[-1] in current


def test_markdown_heading_starts_a_new_chunk_with_its_section() -> None:
    text = "Intro paragraph.\n\n# Pricing\n\nPlans start at ten dollars."
    chunks = chunk_text(text, target=TARGET, overlap=0)
    assert chunks[0] == "Intro paragraph."
    assert "# Pricing" in chunks[1] and "Plans start at ten dollars." in chunks[1]


def test_oversized_words_are_hard_cut() -> None:
    chunks = chunk_text("x" * 1000, target=TARGET, overlap=0)
    assert all(len(c) <= TARGET for c in chunks)
    assert "".join(chunks) == "x" * 1000


@given(st.text(alphabet=st.characters(codec="utf-8", categories=["L", "N", "Zs"]) | st.just("\n"), max_size=3000))
def test_chunks_are_non_empty_bounded_and_cover_every_word(text: str) -> None:
    chunks = chunk_text(text, target=TARGET, overlap=OVERLAP)
    assert all(c.strip() for c in chunks)
    assert all(len(c) <= TARGET + OVERLAP + 1 for c in chunks)
    joined = " ".join(chunks)
    for word in text.split():
        if len(word) <= TARGET:
            assert word in joined
```

```python file=backend/tests/unit/test_extract.py
import pytest

from app.ingestion.extract import ExtractionError, extract_text
from tests.fakes import encrypt_pdf, make_pdf


def test_text_and_markdown_are_decoded_as_utf8() -> None:
    assert extract_text("txt", "café menu".encode(), max_pdf_pages=10) == "café menu"
    assert extract_text("md", b"# Title\n\nBody", max_pdf_pages=10) == "# Title\n\nBody"


@pytest.mark.parametrize(
    ("data", "message"),
    [(b"\xff\xfe\x00bad", "UTF-8"), (b"ok\x00ok", "NUL")],
)
def test_bad_text_is_rejected(data: bytes, message: str) -> None:
    with pytest.raises(ExtractionError, match=message):
        extract_text("txt", data, max_pdf_pages=10)


def test_pdf_text_is_extracted_from_every_page() -> None:
    text = extract_text("pdf", make_pdf(["Quarterly revenue grew", "Second page"]), max_pdf_pages=10)
    assert "Quarterly revenue grew" in text
    assert "Second page" in text


def test_encrypted_pdfs_are_rejected() -> None:
    with pytest.raises(ExtractionError, match="encrypted"):
        extract_text("pdf", encrypt_pdf(make_pdf(["secret"])), max_pdf_pages=10)


def test_pdfs_over_the_page_cap_are_rejected() -> None:
    with pytest.raises(ExtractionError, match="more than 2 pages"):
        extract_text("pdf", make_pdf(["a", "b", "c"]), max_pdf_pages=2)


def test_garbage_pdfs_are_rejected() -> None:
    with pytest.raises(ExtractionError, match="readable PDF"):
        extract_text("pdf", b"%PDF-1.4 this is not really a pdf", max_pdf_pages=10)


def test_unknown_kinds_are_rejected() -> None:
    with pytest.raises(ExtractionError):
        extract_text("exe", b"MZ", max_pdf_pages=10)
```

```python file=backend/tests/integration/test_embeddings_model.py
"""Runs the real local embedding model (downloaded once, about 130 MB). Skip with
VEYL_SKIP_MODEL_TESTS=1 when offline."""

import math
import os

import pytest

from app.retrieval.embeddings import FastEmbedEmbedder

pytestmark = [
    pytest.mark.model,
    pytest.mark.skipif(os.environ.get("VEYL_SKIP_MODEL_TESTS") == "1", reason="offline"),
]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def test_bge_small_embeds_384_dims_and_ranks_related_text_higher() -> None:
    embedder = FastEmbedEmbedder("BAAI/bge-small-en-v1.5")
    related, unrelated = embedder.embed_documents(
        [
            "Customers can request a refund within 30 days of purchase.",
            "The cafeteria serves lunch from noon until two.",
        ]
    )
    query = embedder.embed_query("What is the refund policy?")
    assert len(query) == len(related) == 384
    assert _cosine(query, related) > _cosine(query, unrelated)
```

- [ ] **Step 3: Run to verify failure** — `uv run pytest tests/unit/test_chunker.py tests/unit/test_extract.py tests/integration/test_embeddings_model.py -q` → FAIL (modules missing).

- [ ] **Step 4: Implement**

```python file=backend/app/retrieval/embeddings.py
from collections.abc import Sequence
from typing import Any, Protocol

from app.db.schema import EMBEDDING_DIM


class EmbeddingError(RuntimeError):
    pass


class Embedder(Protocol):
    model_name: str

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def _checked(vector: Any) -> list[float]:
    values = [float(x) for x in vector]
    if len(values) != EMBEDDING_DIM:
        raise EmbeddingError(f"expected {EMBEDDING_DIM} dimensions, got {len(values)}")
    return values


class FastEmbedEmbedder:
    """Local ONNX embeddings (no API key, no network after the model is cached)."""

    def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
        from fastembed import TextEmbedding  # heavy import, deferred

        self.model_name = model_name
        self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [_checked(v) for v in self._model.passage_embed(list(texts))]

    def embed_query(self, text: str) -> list[float]:
        return _checked(next(iter(self._model.query_embed(text))))
```

```python file=backend/app/ingestion/extract.py
import io

from pypdf import PdfReader

_TEXT_KINDS = frozenset({"txt", "md"})


class ExtractionError(Exception):
    """Ingestion failed for a reason that is safe to show the uploader."""


def extract_text(kind: str, data: bytes, *, max_pdf_pages: int) -> str:
    if kind == "pdf":
        return _pdf_text(data, max_pdf_pages)
    if kind in _TEXT_KINDS:
        return _utf8_text(data)
    raise ExtractionError(f"unsupported document type: {kind}")


def _utf8_text(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExtractionError("file is not valid UTF-8 text") from exc
    if "\x00" in text:
        raise ExtractionError("file contains NUL bytes")
    return text


def _pdf_text(data: bytes, max_pages: int) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        encrypted = reader.is_encrypted
        page_count = 0 if encrypted else len(reader.pages)
    except Exception as exc:  # malformed input can raise many exception types
        raise ExtractionError("file is not a readable PDF") from exc
    if encrypted:
        raise ExtractionError("encrypted PDFs are not supported")
    if page_count > max_pages:
        raise ExtractionError(f"PDF has more than {max_pages} pages")
    try:
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise ExtractionError("file is not a readable PDF") from exc
```

```python file=backend/app/ingestion/chunker.py
"""Paragraph-aware splitter: paragraphs, then sentences, then hard cuts. Markdown headings
start a new chunk so a section stays with its heading. Consecutive chunks overlap by up to
`overlap` characters (cut at a word boundary)."""

import re
from itertools import pairwise

TARGET_CHARS = 800
OVERLAP_CHARS = 120

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _paragraphs(text: str) -> list[str]:
    normalised = text.replace("\r\n", "\n")
    return [p.strip() for p in _PARAGRAPH_BREAK.split(normalised) if p.strip()]


def _pieces(paragraph: str, target: int) -> list[str]:
    if len(paragraph) <= target:
        return [paragraph]
    pieces: list[str] = []
    for sentence in _SENTENCE_END.split(paragraph):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= target:
            pieces.append(sentence)
        else:
            pieces.extend(sentence[i : i + target] for i in range(0, len(sentence), target))
    return pieces


def _tail(chunk: str, overlap: int) -> str:
    if len(chunk) <= overlap:
        return chunk
    tail = chunk[-overlap:]
    space = tail.find(" ")
    return tail[space + 1 :] if 0 <= space < len(tail) - 1 else tail


def chunk_text(text: str, target: int = TARGET_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal current, size
        if current:
            chunks.append("\n\n".join(current))
        current, size = [], 0

    for paragraph in _paragraphs(text):
        if paragraph.startswith("#"):
            flush()
        for piece in _pieces(paragraph, target):
            separator = 2 if current else 0
            if current and size + separator + len(piece) > target:
                flush()
                separator = 0
            current.append(piece)
            size += separator + len(piece)
    flush()

    if overlap <= 0 or len(chunks) < 2:
        return chunks
    return [chunks[0]] + [
        f"{_tail(previous, overlap)} {chunk}"
        for previous, chunk in pairwise(chunks)
    ]
```

- [ ] **Step 5: Run full suite + lint + types** — Expected: PASS (first run downloads the model for the `model` test).

- [ ] **Step 6: README (ingestion pipeline section), devlog, commit, push**

```bash
git add -A
git commit -m "feat(ingestion): local bge-small embeddings, PDF/TXT/MD extraction, chunker"
git push origin main
```

---

### Task 4: Storage, upload validation, streamed body limit

**Files:**
- Create: `backend/app/documents/__init__.py` (empty), `backend/app/documents/storage.py`, `backend/app/documents/validation.py`, `backend/app/documents/limits.py`, `backend/tests/unit/test_storage.py`, `backend/tests/unit/test_upload_rules.py`, `backend/tests/unit/test_body_limit.py`
- Modify: `README.md`, `docs/devlog.md`

**Interfaces:**
- Produces:
  - `LocalStorage(root)` with `key_for(tenant_id, document_id) -> str`, `save(key, data)`, `read(key) -> bytes`, `delete(key)`; keys must match `uuid/uuid`.
  - `validate_upload(filename, data, *, max_bytes) -> ValidatedUpload(kind, mime_type, size_bytes, sha256, original_filename)`; errors `UploadRejected` (422 `upload_rejected`), `UploadTooLarge` (413 `payload_too_large`); `sanitize_filename(name) -> str`; `KIND_BY_MIME`.
  - `BodySizeLimit(app, *, max_bytes, path_prefix)` pure ASGI middleware.

- [ ] **Step 1: Write failing tests**

```python file=backend/tests/unit/test_storage.py
import uuid
from pathlib import Path

import pytest

from app.documents.storage import LocalStorage


def test_round_trip_under_tenant_and_document_key(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    tenant_id, document_id = uuid.uuid4(), uuid.uuid4()
    key = storage.key_for(tenant_id, document_id)
    storage.save(key, b"hello")
    assert (tmp_path / str(tenant_id) / str(document_id)).read_bytes() == b"hello"
    assert storage.read(key) == b"hello"
    storage.delete(key)
    assert not (tmp_path / str(tenant_id) / str(document_id)).exists()


@pytest.mark.parametrize("key", ["../etc/passwd", "a/b", "/abs/path", f"{uuid.uuid4()}/../x"])
def test_keys_that_are_not_server_generated_are_refused(tmp_path: Path, key: str) -> None:
    with pytest.raises(ValueError):
        LocalStorage(tmp_path).read(key)
```

```python file=backend/tests/unit/test_upload_rules.py
import hashlib

import pytest

from app.documents.validation import (
    UploadRejected,
    UploadTooLarge,
    sanitize_filename,
    validate_upload,
)
from tests.fakes import make_pdf

MAX = 1024


def test_accepts_text_markdown_and_pdf() -> None:
    assert validate_upload("notes.TXT", b"hello", max_bytes=MAX).kind == "txt"
    assert validate_upload("readme.md", b"# hi", max_bytes=MAX).mime_type == "text/markdown"
    pdf = make_pdf(["x"])
    result = validate_upload("report.pdf", pdf, max_bytes=10_000)
    assert (result.kind, result.size_bytes, result.sha256) == (
        "pdf",
        len(pdf),
        hashlib.sha256(pdf).hexdigest(),
    )


@pytest.mark.parametrize(
    ("filename", "data"),
    [
        ("evil.exe", b"MZ"),
        ("page.html", b"<script>"),
        ("noextension", b"hello"),
        ("fake.pdf", b"just text pretending"),
        ("bad.txt", b"\xff\xfe\xfa"),
        ("nul.md", b"a\x00b"),
        ("empty.txt", b""),
    ],
)
def test_rejects_disallowed_or_mislabelled_files(filename: str, data: bytes) -> None:
    with pytest.raises(UploadRejected):
        validate_upload(filename, data, max_bytes=MAX)


def test_rejects_oversized_files() -> None:
    with pytest.raises(UploadTooLarge):
        validate_upload("big.txt", b"x" * (MAX + 1), max_bytes=MAX)


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("../../etc/passwd.txt", "passwd.txt"),
        ("..\\..\\windows\\win.ini.txt", "win.ini.txt"),
        ("report\x00.pdf", "report_.pdf"),
        ("  .hidden.md ", "hidden.md"),
        ("", "upload"),
    ],
)
def test_filenames_are_reduced_to_a_safe_basename(raw: str, clean: str) -> None:
    assert sanitize_filename(raw) == clean
```

```python file=backend/tests/unit/test_body_limit.py
from collections.abc import Iterator

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.documents.limits import BodySizeLimit

LIMIT = 100


def _app() -> FastAPI:
    app = FastAPI()

    @app.post("/limited/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"received": len(await request.body())}

    @app.post("/open/echo")
    async def open_echo(request: Request) -> dict[str, int]:
        return {"received": len(await request.body())}

    app.add_middleware(BodySizeLimit, max_bytes=LIMIT, path_prefix="/limited")
    return app


def _chunks(total: int) -> Iterator[bytes]:
    for _ in range(total // 10):
        yield b"x" * 10


def test_body_within_limit_passes() -> None:
    with TestClient(_app()) as client:
        assert client.post("/limited/echo", content=b"x" * LIMIT).json() == {"received": LIMIT}


def test_declared_oversized_body_is_rejected() -> None:
    with TestClient(_app()) as client:
        r = client.post("/limited/echo", content=b"x" * (LIMIT + 1))
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"


def test_streamed_body_over_limit_is_rejected_without_content_length() -> None:
    with TestClient(_app()) as client:
        r = client.post("/limited/echo", content=_chunks(LIMIT * 5))
    assert r.status_code == 413


def test_other_paths_are_not_limited() -> None:
    with TestClient(_app()) as client:
        assert client.post("/open/echo", content=b"x" * (LIMIT * 3)).status_code == 200
```

- [ ] **Step 2: Run to verify failure** — FAIL (`No module named 'app.documents'`).

- [ ] **Step 3: Implement**

```python file=backend/app/documents/storage.py
import os
import re
from pathlib import Path
from uuid import UUID

_KEY = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}/[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")


class LocalStorage:
    """Original uploads on a local volume. Keys are always `tenant_id/document_id`, generated
    by the server; user-supplied filenames never reach the filesystem."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @staticmethod
    def key_for(tenant_id: UUID, document_id: UUID) -> str:
        return f"{tenant_id}/{document_id}"

    def _path(self, key: str) -> Path:
        if not _KEY.fullmatch(key):
            raise ValueError("invalid storage key")
        return self.root / key

    def save(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, path)

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)
```

```python file=backend/app/documents/validation.py
import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.errors import AppError

MIME_BY_KIND = {"pdf": "application/pdf", "txt": "text/plain", "md": "text/markdown"}
KIND_BY_MIME = {mime: kind for kind, mime in MIME_BY_KIND.items()}
_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]")


class UploadRejected(AppError):
    status_code = 422
    code = "upload_rejected"
    default_message = "The file was rejected."


class UploadTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"
    default_message = "Upload exceeds the size limit."


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    kind: str
    mime_type: str
    size_bytes: int
    sha256: str
    original_filename: str


def sanitize_filename(name: str) -> str:
    """Keep only a safe basename for display. It is never used as a path."""
    base = PurePosixPath(name.replace("\\", "/")).name
    cleaned = _UNSAFE.sub("_", base).strip(" .")
    return (cleaned or "upload")[:255]


def validate_upload(filename: str, data: bytes, *, max_bytes: int) -> ValidatedUpload:
    if len(data) > max_bytes:
        raise UploadTooLarge()
    if not data:
        raise UploadRejected("The file is empty.")
    safe_name = sanitize_filename(filename)
    kind = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
    if kind not in MIME_BY_KIND:
        raise UploadRejected("Only PDF, TXT, and Markdown files are accepted.")
    if kind == "pdf":
        if not data.startswith(b"%PDF-"):
            raise UploadRejected("The file is not a PDF.")
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UploadRejected("Text files must be UTF-8.") from exc
        if "\x00" in text:
            raise UploadRejected("Text files must not contain NUL bytes.")
    return ValidatedUpload(
        kind=kind,
        mime_type=MIME_BY_KIND[kind],
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        original_filename=safe_name,
    )
```

```python file=backend/app/documents/limits.py
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _BodyTooLarge(Exception):
    pass


class BodySizeLimit:
    """Rejects request bodies over `max_bytes` while they stream in, without trusting
    Content-Length, so an oversized upload is refused before it is buffered to disk."""

    def __init__(self, app: ASGIApp, *, max_bytes: int, path_prefix: str) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.path_prefix = path_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self.path_prefix):
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(scope, receive, send)
            return

        received = 0
        exceeded = False
        started = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    exceeded = True
                    raise _BodyTooLarge()
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            # Frameworks may turn the receive error into their own response; replace it.
            if exceeded:
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLarge:
            pass
        if exceeded and not started:
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            {
                "error": {
                    "code": "payload_too_large",
                    "message": "Upload exceeds the size limit.",
                    "trace_id": None,
                }
            },
            status_code=413,
        )
        await response(scope, receive, send)
```

- [ ] **Step 4: Run full suite + lint + types** — Expected: PASS.

- [ ] **Step 5: README (upload rules), devlog, commit, push**

```bash
git add -A
git commit -m "feat(documents): server-keyed storage, upload validation, streamed body limit"
git push origin main
```

---

### Task 5: Documents API

**Files:**
- Create: `backend/app/documents/service.py`, `backend/app/documents/router.py`, `backend/tests/integration/test_documents_api.py`, `backend/tests/security/test_upload_validation.py`
- Modify (full replacement): `backend/app/main.py`, `backend/tests/api.py`
- Modify: `README.md`, `docs/devlog.md`

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces:
  - `POST /api/v1/documents` (multipart: `file`, `title`, `classification`, `allowed_roles` repeated, `allowed_user_ids` repeated) → 202 `DocumentOut`.
  - `GET /api/v1/documents`, `GET /api/v1/documents/{id}`, `PUT /api/v1/documents/{id}/permissions` `{classification, allowed_roles, allowed_user_ids}`, `DELETE /api/v1/documents/{id}` → 204.
  - `DocumentOut`: `id, title, original_filename, mime_type, size_bytes, classification, status, error, uploaded_by, created_at, updated_at, can_manage, allowed_roles|None, allowed_user_ids|None` (ACL only when `can_manage`).
  - `app.documents.service`: `DocumentRecord`, `upload_document(engine, storage, ctx, *, upload, data, title, classification, allowed_roles, allowed_user_ids)`, `list_documents`, `get_document`, `update_permissions`, `delete_document`, `can_manage(ctx, record)`; error `UnknownUsers` (422 `unknown_users`).
  - `app.state.storage`; `BodySizeLimit` mounted on `/api/v1/documents`.
  - `tests.api.upload(client, filename, data, *, title, classification, roles, user_ids, extra)`.

- [ ] **Step 1: Test helpers** (full replacement)

```python file=backend/tests/api.py
from collections.abc import Iterable
from typing import Any
from uuid import UUID

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


def upload(
    client: TestClient,
    filename: str,
    data: bytes,
    *,
    title: str = "Doc",
    classification: str = "internal",
    roles: Iterable[str] = (),
    user_ids: Iterable[UUID] = (),
    extra: dict[str, str] | None = None,
) -> Any:
    form: dict[str, Any] = {
        "title": title,
        "classification": classification,
        "allowed_roles": list(roles),
        "allowed_user_ids": [str(u) for u in user_ids],
        **(extra or {}),
    }
    return client.post(
        f"{API}/documents",
        files={"file": (filename, data, "application/octet-stream")},
        data=form,
        headers=csrf(client),
    )
```

- [ ] **Step 2: Write failing tests**

```python file=backend/tests/integration/test_documents_api.py
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select

from app.authz import Classification, Role
from app.db.schema import document_chunks, documents, ingestion_jobs, tenants
from tests.api import API, client_for, csrf, login, upload
from tests.conftest import DatabaseUrls
from tests.factories import (
    SeededTenant,
    add_chunks,
    create_document,
    create_tenant_with_admin,
    create_user,
    unit_vector,
)

pytestmark = pytest.mark.integration


def _policy_version(owner_engine: Engine, tenant_id: UUID) -> int:
    with owner_engine.connect() as conn:
        return int(
            conn.execute(select(tenants.c.policy_version).where(tenants.c.id == tenant_id)).scalar_one()
        )


@pytest.fixture
def seeded(owner_engine: Engine) -> SeededTenant:
    return create_tenant_with_admin(owner_engine)


def test_manager_upload_is_stored_queued_and_owned(
    owner_engine: Engine, database_urls: DatabaseUrls, seeded: SeededTenant, tmp_path: Path
) -> None:
    manager, email = create_user(owner_engine, seeded.tenant_id, role=Role.MANAGER)
    with client_for(database_urls.app, upload_dir=str(tmp_path)) as client:
        login(client, seeded.slug, email)
        r = upload(client, "plan.txt", b"Roadmap for Q4", title="Roadmap", classification="confidential", roles=["manager"])
    assert r.status_code == 202, r.text
    body = r.json()
    assert (body["status"], body["classification"], body["can_manage"]) == ("pending", "confidential", True)
    assert body["allowed_roles"] == ["manager"]
    assert str(manager) in body["allowed_user_ids"]
    document_id = UUID(body["id"])
    assert (tmp_path / str(seeded.tenant_id) / str(document_id)).read_bytes() == b"Roadmap for Q4"
    with owner_engine.connect() as conn:
        jobs = conn.execute(select(ingestion_jobs.c.status).where(ingestion_jobs.c.document_id == document_id)).scalars().all()
    assert jobs == ["queued"]


@pytest.mark.parametrize(
    ("role", "classification", "status"),
    [
        (Role.MEMBER, "public", 403),
        (Role.MANAGER, "restricted", 403),
        (Role.MANAGER, "confidential", 202),
        (Role.ADMIN, "restricted", 202),
    ],
)
def test_upload_requires_role_and_clearance(
    owner_engine: Engine,
    database_urls: DatabaseUrls,
    seeded: SeededTenant,
    tmp_path: Path,
    role: Role,
    classification: str,
    status: int,
) -> None:
    _, email = create_user(owner_engine, seeded.tenant_id, role=role)
    with client_for(database_urls.app, upload_dir=str(tmp_path)) as client:
        login(client, seeded.slug, email)
        assert upload(client, "a.txt", b"text", classification=classification).status_code == status


def test_grants_to_users_outside_the_tenant_are_rejected(
    owner_engine: Engine, database_urls: DatabaseUrls, seeded: SeededTenant, tmp_path: Path
) -> None:
    other = create_tenant_with_admin(owner_engine)
    with client_for(database_urls.app, upload_dir=str(tmp_path)) as client:
        login(client, seeded.slug, seeded.admin_email)
        r = upload(client, "a.txt", b"text", user_ids=[other.admin_id])
    assert (r.status_code, r.json()["error"]["code"]) == (422, "unknown_users")
    assert not any(p.is_file() for p in tmp_path.rglob("*"))


def test_listing_shows_only_readable_documents_and_hides_acls_from_readers(
    owner_engine: Engine, database_urls: DatabaseUrls, seeded: SeededTenant
) -> None:
    _, member_email = create_user(owner_engine, seeded.tenant_id, role=Role.MEMBER)
    shared = create_document(owner_engine, seeded.tenant_id, seeded.admin_id, allowed_roles=[Role.MEMBER], title="Handbook")
    create_document(owner_engine, seeded.tenant_id, seeded.admin_id, classification=Classification.RESTRICTED, allowed_roles=[Role.ADMIN], title="Board")
    with client_for(database_urls.app) as client:
        login(client, seeded.slug, member_email)
        listed = client.get(f"{API}/documents").json()
    assert [d["id"] for d in listed] == [str(shared)]
    assert listed[0]["allowed_roles"] is None and listed[0]["can_manage"] is False


def test_unreadable_and_foreign_documents_are_not_found(
    owner_engine: Engine, database_urls: DatabaseUrls, seeded: SeededTenant
) -> None:
    other = create_tenant_with_admin(owner_engine)
    foreign = create_document(owner_engine, other.tenant_id, other.admin_id, allowed_roles=list(Role))
    hidden = create_document(owner_engine, seeded.tenant_id, seeded.admin_id, allowed_roles=[Role.ADMIN])
    _, member_email = create_user(owner_engine, seeded.tenant_id, role=Role.MEMBER)
    with client_for(database_urls.app) as client:
        login(client, seeded.slug, member_email)
        for document_id in (foreign, hidden):
            r = client.get(f"{API}/documents/{document_id}")
            assert (r.status_code, r.json()["error"]["code"]) == (404, "not_found")


def test_permission_change_rewrites_chunk_acls_and_bumps_policy_version(
    owner_engine: Engine, database_urls: DatabaseUrls, seeded: SeededTenant
) -> None:
    member, _ = create_user(owner_engine, seeded.tenant_id, role=Role.MEMBER)
    document_id = create_document(owner_engine, seeded.tenant_id, seeded.admin_id, allowed_roles=[Role.MEMBER])
    add_chunks(owner_engine, document_id, ["a", "b"], [unit_vector(1), unit_vector(2)])
    before = _policy_version(owner_engine, seeded.tenant_id)
    with client_for(database_urls.app) as client:
        login(client, seeded.slug, seeded.admin_email)
        r = client.put(
            f"{API}/documents/{document_id}/permissions",
            json={"classification": "confidential", "allowed_roles": ["manager"], "allowed_user_ids": [str(member)]},
            headers=csrf(client),
        )
    assert r.status_code == 200, r.text
    with owner_engine.connect() as conn:
        doc = conn.execute(select(documents.c.acl_version, documents.c.allowed_user_ids).where(documents.c.id == document_id)).one()
        chunks = conn.execute(
            select(document_chunks.c.classification, document_chunks.c.allowed_roles, document_chunks.c.allowed_user_ids, document_chunks.c.acl_version)
            .where(document_chunks.c.document_id == document_id)
        ).all()
    assert doc.acl_version == 2
    assert set(doc.allowed_user_ids) == {member, seeded.admin_id}
    assert all(
        (c.classification, c.allowed_roles, set(c.allowed_user_ids), c.acl_version)
        == ("confidential", ["manager"], {member, seeded.admin_id}, 2)
        for c in chunks
    )
    assert _policy_version(owner_engine, seeded.tenant_id) == before + 1


def test_managers_manage_only_their_own_uploads_within_clearance(
    owner_engine: Engine, database_urls: DatabaseUrls, seeded: SeededTenant
) -> None:
    manager, email = create_user(owner_engine, seeded.tenant_id, role=Role.MANAGER)
    own = create_document(owner_engine, seeded.tenant_id, manager, allowed_user_ids=[manager])
    others = create_document(owner_engine, seeded.tenant_id, seeded.admin_id, allowed_roles=[Role.MANAGER])
    body = {"classification": "internal", "allowed_roles": ["member"], "allowed_user_ids": []}
    with client_for(database_urls.app) as client:
        login(client, seeded.slug, email)
        h = csrf(client)
        assert client.put(f"{API}/documents/{own}/permissions", json=body, headers=h).status_code == 200
        assert client.put(f"{API}/documents/{others}/permissions", json=body, headers=h).status_code == 403
        raise_it = {**body, "classification": "restricted"}
        assert client.put(f"{API}/documents/{own}/permissions", json=raise_it, headers=h).status_code == 403


def test_delete_hides_document_removes_chunks_and_file(
    owner_engine: Engine, database_urls: DatabaseUrls, seeded: SeededTenant, tmp_path: Path
) -> None:
    with client_for(database_urls.app, upload_dir=str(tmp_path)) as client:
        login(client, seeded.slug, seeded.admin_email)
        document_id = upload(client, "a.txt", b"temporary", roles=["member"]).json()["id"]
        add_chunks(owner_engine, UUID(document_id), ["temporary"], [unit_vector(3)])
        before = _policy_version(owner_engine, seeded.tenant_id)
        assert client.delete(f"{API}/documents/{document_id}", headers=csrf(client)).status_code == 204
        assert client.get(f"{API}/documents/{document_id}").status_code == 404
    with owner_engine.connect() as conn:
        remaining = conn.execute(
            select(func.count()).select_from(document_chunks).where(document_chunks.c.document_id == UUID(document_id))
        ).scalar_one()
    assert remaining == 0
    assert not (tmp_path / str(seeded.tenant_id) / document_id).exists()
    assert _policy_version(owner_engine, seeded.tenant_id) == before + 1
```

```python file=backend/tests/security/test_upload_validation.py
"""T11 (insecure document upload) and T4 (forged tenant identifiers on upload)."""

from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select

from app.db.schema import documents
from tests.api import client_for, login, upload
from tests.conftest import DatabaseUrls
from tests.factories import SeededTenant, create_tenant_with_admin

pytestmark = pytest.mark.integration


@pytest.fixture
def seeded(owner_engine: Engine) -> SeededTenant:
    return create_tenant_with_admin(owner_engine)


def _document_count(owner_engine: Engine, tenant_id: UUID) -> int:
    with owner_engine.connect() as conn:
        return int(
            conn.execute(select(func.count()).select_from(documents).where(documents.c.tenant_id == tenant_id)).scalar_one()
        )


def test_oversized_upload_is_rejected_and_nothing_is_stored(
    owner_engine: Engine, database_urls: DatabaseUrls, seeded: SeededTenant, tmp_path: Path
) -> None:
    with client_for(database_urls.app, upload_dir=str(tmp_path), max_upload_mb=1) as client:
        login(client, seeded.slug, seeded.admin_email)
        r = upload(client, "big.txt", b"x" * (2 * 1024 * 1024))
    assert r.status_code == 413
    assert not any(p.is_file() for p in tmp_path.rglob("*"))
    assert _document_count(owner_engine, seeded.tenant_id) == 0


@pytest.mark.parametrize(
    ("filename", "data"),
    [("run.exe", b"MZ"), ("fake.pdf", b"not a pdf"), ("nul.txt", b"a\x00b"), ("latin.txt", b"\xe9t\xe9")],
)
def test_disallowed_files_are_rejected(
    owner_engine: Engine, database_urls: DatabaseUrls, seeded: SeededTenant, tmp_path: Path, filename: str, data: bytes
) -> None:
    with client_for(database_urls.app, upload_dir=str(tmp_path)) as client:
        login(client, seeded.slug, seeded.admin_email)
        r = upload(client, filename, data)
    assert (r.status_code, r.json()["error"]["code"]) == (422, "upload_rejected")
    assert _document_count(owner_engine, seeded.tenant_id) == 0


def test_hostile_filename_is_metadata_only(
    database_urls: DatabaseUrls, seeded: SeededTenant, tmp_path: Path
) -> None:
    with client_for(database_urls.app, upload_dir=str(tmp_path / "store")) as client:
        login(client, seeded.slug, seeded.admin_email)
        r = upload(client, "../../../escaped.txt", b"content")
    assert r.status_code == 202
    assert r.json()["original_filename"] == "escaped.txt"
    stored = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert stored == [tmp_path / "store" / str(seeded.tenant_id) / r.json()["id"]]


def test_forged_tenant_field_is_ignored(
    owner_engine: Engine, database_urls: DatabaseUrls, seeded: SeededTenant, tmp_path: Path
) -> None:
    other = create_tenant_with_admin(owner_engine)
    with client_for(database_urls.app, upload_dir=str(tmp_path)) as client:
        login(client, seeded.slug, seeded.admin_email)
        r = upload(client, "a.txt", b"text", extra={"tenant_id": str(other.tenant_id), "uploaded_by": str(other.admin_id)})
    assert r.status_code == 202
    assert r.json()["uploaded_by"] == str(seeded.admin_id)
    assert _document_count(owner_engine, other.tenant_id) == 0
    assert _document_count(owner_engine, seeded.tenant_id) == 1
```

- [ ] **Step 3: Run to verify failure** — FAIL (404 on `/api/v1/documents`).

- [ ] **Step 4: Implement**

```python file=backend/app/documents/service.py
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, delete, func, insert, select, update

from app.authz import Action, AuthContext, Classification, DocumentRef, Role, authorize, is_allowed
from app.authz.sql import document_read_filter
from app.db.schema import document_chunks, documents, ingestion_jobs, users
from app.db.tenancy import bump_policy_version, tenant_transaction
from app.documents.storage import LocalStorage
from app.documents.validation import ValidatedUpload
from app.errors import AppError, NotFound


class UnknownUsers(AppError):
    status_code = 422
    code = "unknown_users"
    default_message = "Some granted users do not exist in this workspace."


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    id: UUID
    title: str
    original_filename: str
    mime_type: str
    size_bytes: int
    classification: Classification
    allowed_roles: list[Role]
    allowed_user_ids: list[UUID]
    status: str
    error: str | None
    uploaded_by: UUID
    created_at: datetime
    updated_at: datetime


_COLUMNS = (
    documents.c.id,
    documents.c.title,
    documents.c.original_filename,
    documents.c.mime_type,
    documents.c.size_bytes,
    documents.c.classification,
    documents.c.allowed_roles,
    documents.c.allowed_user_ids,
    documents.c.status,
    documents.c.error,
    documents.c.uploaded_by,
    documents.c.created_at,
    documents.c.updated_at,
)


def _record(row: Any) -> DocumentRecord:
    return DocumentRecord(
        id=row.id,
        title=row.title,
        original_filename=row.original_filename,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        classification=Classification(row.classification),
        allowed_roles=[Role(r) for r in row.allowed_roles],
        allowed_user_ids=list(row.allowed_user_ids),
        status=row.status,
        error=row.error,
        uploaded_by=row.uploaded_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _ref(ctx: AuthContext, uploaded_by: UUID, classification: Classification) -> DocumentRef:
    return DocumentRef(tenant_id=ctx.tenant_id, uploaded_by=uploaded_by, classification=classification)


def can_manage(ctx: AuthContext, record: DocumentRecord) -> bool:
    return is_allowed(ctx, Action.DOCUMENT_MANAGE, _ref(ctx, record.uploaded_by, record.classification))


def _acl(
    roles: Iterable[Role], user_ids: Iterable[UUID], uploaded_by: UUID
) -> tuple[list[str], list[UUID]]:
    """Normalise an ACL. The uploader always keeps access (spec section 4.3)."""
    return sorted({r.value for r in roles}), sorted({*user_ids, uploaded_by}, key=str)


def _require_tenant_users(conn: Connection, tenant_id: UUID, user_ids: list[UUID]) -> None:
    found = set(
        conn.execute(
            select(users.c.id).where(users.c.tenant_id == tenant_id, users.c.id.in_(user_ids))
        ).scalars()
    )
    if found != set(user_ids):
        raise UnknownUsers()


def upload_document(
    engine: Engine,
    storage: LocalStorage,
    ctx: AuthContext,
    *,
    upload: ValidatedUpload,
    data: bytes,
    title: str,
    classification: Classification,
    allowed_roles: Iterable[Role],
    allowed_user_ids: Iterable[UUID],
) -> DocumentRecord:
    authorize(ctx, Action.DOCUMENT_UPLOAD, _ref(ctx, ctx.user_id, classification))
    roles, user_ids = _acl(allowed_roles, allowed_user_ids, ctx.user_id)
    document_id = uuid4()
    key = LocalStorage.key_for(ctx.tenant_id, document_id)
    storage.save(key, data)
    try:
        with tenant_transaction(engine, ctx.tenant_id) as conn:
            _require_tenant_users(conn, ctx.tenant_id, user_ids)
            row = conn.execute(
                insert(documents)
                .values(
                    id=document_id,
                    tenant_id=ctx.tenant_id,
                    uploaded_by=ctx.user_id,
                    title=title,
                    original_filename=upload.original_filename,
                    mime_type=upload.mime_type,
                    size_bytes=upload.size_bytes,
                    sha256=upload.sha256,
                    storage_key=key,
                    classification=classification.value,
                    allowed_roles=roles,
                    allowed_user_ids=user_ids,
                )
                .returning(*_COLUMNS)
            ).one()
            conn.execute(insert(ingestion_jobs).values(tenant_id=ctx.tenant_id, document_id=document_id))
    except BaseException:
        storage.delete(key)
        raise
    return _record(row)


def list_documents(engine: Engine, ctx: AuthContext) -> list[DocumentRecord]:
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        rows = conn.execute(
            select(*_COLUMNS).where(document_read_filter(ctx)).order_by(documents.c.created_at.desc())
        ).all()
    return [_record(r) for r in rows]


def _readable_row(conn: Connection, ctx: AuthContext, document_id: UUID, *, lock: bool) -> Any:
    query = select(*_COLUMNS, documents.c.storage_key).where(
        documents.c.id == document_id, document_read_filter(ctx)
    )
    if lock:
        query = query.with_for_update(key_share=True)
    row = conn.execute(query).one_or_none()
    if row is None:
        raise NotFound()
    return row


def get_document(engine: Engine, ctx: AuthContext, document_id: UUID) -> DocumentRecord:
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        return _record(_readable_row(conn, ctx, document_id, lock=False))


def update_permissions(
    engine: Engine,
    ctx: AuthContext,
    document_id: UUID,
    *,
    classification: Classification,
    allowed_roles: Iterable[Role],
    allowed_user_ids: Iterable[UUID],
) -> DocumentRecord:
    """Spec section 5.4: document ACL, every chunk's ACL copy, acl_version, and the tenant
    policy_version change in one transaction."""
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        current = _readable_row(conn, ctx, document_id, lock=True)
        authorize(ctx, Action.DOCUMENT_MANAGE, _ref(ctx, current.uploaded_by, Classification(current.classification)))
        authorize(ctx, Action.DOCUMENT_MANAGE, _ref(ctx, current.uploaded_by, classification))
        roles, user_ids = _acl(allowed_roles, allowed_user_ids, current.uploaded_by)
        _require_tenant_users(conn, ctx.tenant_id, user_ids)
        row = conn.execute(
            update(documents)
            .where(documents.c.id == document_id)
            .values(
                classification=classification.value,
                allowed_roles=roles,
                allowed_user_ids=user_ids,
                acl_version=documents.c.acl_version + 1,
                updated_at=func.now(),
            )
            .returning(*_COLUMNS, documents.c.acl_version)
        ).one()
        conn.execute(
            update(document_chunks)
            .where(document_chunks.c.document_id == document_id, document_chunks.c.tenant_id == ctx.tenant_id)
            .values(
                classification=classification.value,
                allowed_roles=roles,
                allowed_user_ids=user_ids,
                acl_version=row.acl_version,
            )
        )
        bump_policy_version(conn, ctx.tenant_id)
    return _record(row)


def delete_document(engine: Engine, storage: LocalStorage, ctx: AuthContext, document_id: UUID) -> None:
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        current = _readable_row(conn, ctx, document_id, lock=True)
        authorize(ctx, Action.DOCUMENT_MANAGE, _ref(ctx, current.uploaded_by, Classification(current.classification)))
        conn.execute(
            update(documents)
            .where(documents.c.id == document_id)
            .values(deleted_at=func.now(), updated_at=func.now())
        )
        conn.execute(delete(document_chunks).where(document_chunks.c.document_id == document_id))
        bump_policy_version(conn, ctx.tenant_id)
    storage.delete(current.storage_key)
```

```python file=backend/app/documents/router.py
from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from app.auth.deps import CurrentUser, EngineDep, SettingsDep, require_csrf
from app.authz import AuthContext, Classification, Role
from app.documents import service
from app.documents.storage import LocalStorage
from app.documents.validation import validate_upload

router = APIRouter(prefix="/documents", tags=["documents"])


class DocumentOut(BaseModel):
    id: UUID
    title: str
    original_filename: str
    mime_type: str
    size_bytes: int
    classification: Classification
    status: str
    error: str | None
    uploaded_by: UUID
    created_at: datetime
    updated_at: datetime
    can_manage: bool
    allowed_roles: list[Role] | None
    allowed_user_ids: list[UUID] | None


class PermissionsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification: Classification
    allowed_roles: list[Role] = Field(default_factory=list, max_length=3)
    allowed_user_ids: list[UUID] = Field(default_factory=list, max_length=500)


def _storage(request: Request) -> LocalStorage:
    return cast(LocalStorage, request.app.state.storage)


def _out(ctx: AuthContext, record: service.DocumentRecord) -> DocumentOut:
    manage = service.can_manage(ctx, record)
    return DocumentOut(
        id=record.id,
        title=record.title,
        original_filename=record.original_filename,
        mime_type=record.mime_type,
        size_bytes=record.size_bytes,
        classification=record.classification,
        status=record.status,
        error=record.error,
        uploaded_by=record.uploaded_by,
        created_at=record.created_at,
        updated_at=record.updated_at,
        can_manage=manage,
        allowed_roles=record.allowed_roles if manage else None,
        allowed_user_ids=record.allowed_user_ids if manage else None,
    )


@router.post("", status_code=202, dependencies=[Depends(require_csrf)])
def upload_document(
    ctx: CurrentUser,
    engine: EngineDep,
    settings: SettingsDep,
    request: Request,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form(min_length=1, max_length=300)],
    classification: Annotated[Classification, Form()],
    allowed_roles: Annotated[list[Role] | None, Form()] = None,
    allowed_user_ids: Annotated[list[UUID] | None, Form()] = None,
) -> DocumentOut:
    max_bytes = settings.max_upload_mb * 1024 * 1024
    data = file.file.read(max_bytes + 1)
    validated = validate_upload(file.filename or "", data, max_bytes=max_bytes)
    record = service.upload_document(
        engine,
        _storage(request),
        ctx,
        upload=validated,
        data=data,
        title=title,
        classification=classification,
        allowed_roles=allowed_roles or [],
        allowed_user_ids=allowed_user_ids or [],
    )
    return _out(ctx, record)


@router.get("")
def list_documents(ctx: CurrentUser, engine: EngineDep) -> list[DocumentOut]:
    return [_out(ctx, r) for r in service.list_documents(engine, ctx)]


@router.get("/{document_id}")
def get_document(document_id: UUID, ctx: CurrentUser, engine: EngineDep) -> DocumentOut:
    return _out(ctx, service.get_document(engine, ctx, document_id))


@router.put("/{document_id}/permissions", dependencies=[Depends(require_csrf)])
def update_permissions(
    document_id: UUID, body: PermissionsUpdate, ctx: CurrentUser, engine: EngineDep
) -> DocumentOut:
    record = service.update_permissions(
        engine,
        ctx,
        document_id,
        classification=body.classification,
        allowed_roles=body.allowed_roles,
        allowed_user_ids=body.allowed_user_ids,
    )
    return _out(ctx, record)


@router.delete("/{document_id}", status_code=204, dependencies=[Depends(require_csrf)])
def delete_document(
    document_id: UUID, ctx: CurrentUser, engine: EngineDep, request: Request
) -> Response:
    service.delete_document(engine, _storage(request), ctx, document_id)
    return Response(status_code=204)
```

```python file=backend/app/main.py
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from app import health
from app.auth import router as auth_router
from app.auth.ratelimit import LoginLimiters
from app.config import Settings
from app.db.engine import create_db_engine
from app.documents import router as documents_router
from app.documents.limits import BodySizeLimit
from app.documents.storage import LocalStorage
from app.errors import register_error_handlers
from app.tenants import router as tenants_router
from app.users import router as users_router

API_PREFIX = "/api/v1"
MULTIPART_OVERHEAD_BYTES = 64 * 1024


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
    app.state.login_limiters = LoginLimiters.create(
        max_failures=settings.login_max_failures,
        window_seconds=settings.login_window_seconds,
        max_concurrent_hashes=settings.login_max_concurrent_hashes,
    )
    app.state.storage = LocalStorage(settings.upload_dir)
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

    app.add_middleware(
        BodySizeLimit,
        max_bytes=settings.max_upload_mb * 1024 * 1024 + MULTIPART_OVERHEAD_BYTES,
        path_prefix=f"{API_PREFIX}/documents",
    )

    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(auth_router.router, prefix=API_PREFIX)
    app.include_router(tenants_router.router, prefix=API_PREFIX)
    app.include_router(users_router.router, prefix=API_PREFIX)
    app.include_router(documents_router.router, prefix=API_PREFIX)
    return app
```

- [ ] **Step 5: Run full suite + lint + types** — Expected: PASS.

- [ ] **Step 6: README (documents endpoints), devlog, commit, push**

```bash
git add -A
git commit -m "feat(api): document upload, listing, permission changes, deletion"
git push origin main
```

---

### Task 6: Ingestion worker

**Files:**
- Create: `backend/app/ingestion/pipeline.py`, `backend/app/worker.py`, `backend/tests/integration/test_ingestion.py`
- Modify: `README.md`, `docs/devlog.md`

**Interfaces:**
- Consumes: claim function (Task 1), extract/chunk/embed (Task 3), storage + validation (Task 4), documents service (Task 5).
- Produces: `app.ingestion.pipeline`: `ClaimedJob`, `PreparedDocument`, `claim_job(engine) -> ClaimedJob | None`, `Ingestor(engine, storage, embedder, *, max_pdf_pages)` with `prepare(job)`, `persist(prepared)`, `fail(job, error)`, `run_once() -> bool`; `MAX_ATTEMPTS = 3`. `python -m app.worker`.

- [ ] **Step 1: Write failing tests**

```python file=backend/tests/integration/test_ingestion.py
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, select, update

from app.authz import AuthContext, Classification, Role
from app.db.schema import document_chunks, documents, ingestion_jobs
from app.documents import service as documents_service
from app.documents.storage import LocalStorage
from app.documents.validation import validate_upload
from app.ingestion.pipeline import MAX_ATTEMPTS, Ingestor, claim_job
from tests.factories import SeededTenant, create_tenant_with_admin, create_user, drain_jobs
from tests.fakes import FakeEmbedder, encrypt_pdf, make_pdf

pytestmark = pytest.mark.integration


@pytest.fixture
def seeded(owner_engine: Engine) -> SeededTenant:
    drain_jobs(owner_engine)
    return create_tenant_with_admin(owner_engine)


def _admin(seeded: SeededTenant) -> AuthContext:
    return AuthContext(user_id=seeded.admin_id, tenant_id=seeded.tenant_id, role=Role.ADMIN, session_id=uuid4())


def _upload(app_engine: Engine, storage: LocalStorage, ctx: AuthContext, filename: str, data: bytes, **acl: object) -> UUID:
    record = documents_service.upload_document(
        app_engine,
        storage,
        ctx,
        upload=validate_upload(filename, data, max_bytes=10_000_000),
        data=data,
        title=filename,
        classification=acl.get("classification", Classification.INTERNAL),  # type: ignore[arg-type]
        allowed_roles=acl.get("roles", [Role.MEMBER]),  # type: ignore[arg-type]
        allowed_user_ids=[],
    )
    return record.id


def _document(owner_engine: Engine, document_id: UUID) -> Any:
    with owner_engine.connect() as conn:
        return conn.execute(select(documents).where(documents.c.id == document_id)).one()


def _chunks(owner_engine: Engine, document_id: UUID) -> list[Any]:
    with owner_engine.connect() as conn:
        return list(
            conn.execute(
                select(document_chunks).where(document_chunks.c.document_id == document_id).order_by(document_chunks.c.chunk_index)
            ).all()
        )


def test_text_document_is_chunked_embedded_and_ready(
    owner_engine: Engine, app_engine: Engine, seeded: SeededTenant, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path)
    text = "\n\n".join(f"Section {i}: the travel policy covers flights and hotels." for i in range(40))
    document_id = _upload(app_engine, storage, _admin(seeded), "policy.md", text.encode())
    ingestor = Ingestor(app_engine, storage, FakeEmbedder(), max_pdf_pages=50)
    assert ingestor.run_once() is True
    assert ingestor.run_once() is False
    doc = _document(owner_engine, document_id)
    chunks = _chunks(owner_engine, document_id)
    assert doc.status == "ready"
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.allowed_roles == doc.allowed_roles
        assert chunk.allowed_user_ids == doc.allowed_user_ids
        assert chunk.classification == doc.classification
        assert chunk.acl_version == doc.acl_version
        assert len(chunk.embedding) == 384


def test_pdf_text_reaches_chunks(owner_engine: Engine, app_engine: Engine, seeded: SeededTenant, tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    document_id = _upload(app_engine, storage, _admin(seeded), "q3.pdf", make_pdf(["Quarterly revenue grew 12 percent"]))
    Ingestor(app_engine, storage, FakeEmbedder(), max_pdf_pages=50).run_once()
    assert "Quarterly revenue grew 12 percent" in _chunks(owner_engine, document_id)[0].content


def test_encrypted_pdf_fails_permanently_with_a_safe_message(
    owner_engine: Engine, app_engine: Engine, seeded: SeededTenant, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path)
    document_id = _upload(app_engine, storage, _admin(seeded), "locked.pdf", encrypt_pdf(make_pdf(["secret"])))
    Ingestor(app_engine, storage, FakeEmbedder(), max_pdf_pages=50).run_once()
    doc = _document(owner_engine, document_id)
    assert (doc.status, doc.error) == ("failed", "encrypted PDFs are not supported")
    assert _chunks(owner_engine, document_id) == []
    with owner_engine.connect() as conn:
        assert conn.execute(select(ingestion_jobs.c.status).where(ingestion_jobs.c.document_id == document_id)).scalar_one() == "failed"


def test_transient_failures_retry_then_fail(
    owner_engine: Engine, app_engine: Engine, seeded: SeededTenant, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path)
    document_id = _upload(app_engine, storage, _admin(seeded), "a.txt", b"hello world")
    ingestor = Ingestor(app_engine, storage, FakeEmbedder(fail=True), max_pdf_pages=50)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        assert ingestor.run_once() is True
        with owner_engine.begin() as conn:
            job = conn.execute(select(ingestion_jobs).where(ingestion_jobs.c.document_id == document_id)).one()
            assert job.attempts == attempt
            conn.execute(update(ingestion_jobs).where(ingestion_jobs.c.id == job.id).values(run_after=func.now()))
    doc = _document(owner_engine, document_id)
    assert (doc.status, doc.error) == ("failed", "ingestion failed")


def test_permission_change_during_ingestion_reaches_new_chunks(
    owner_engine: Engine, app_engine: Engine, seeded: SeededTenant, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path)
    admin = _admin(seeded)
    member, _ = create_user(owner_engine, seeded.tenant_id, role=Role.MEMBER)
    document_id = _upload(app_engine, storage, admin, "a.txt", b"merger terms draft")
    ingestor = Ingestor(app_engine, storage, FakeEmbedder(), max_pdf_pages=50)
    job = claim_job(app_engine)
    assert job is not None
    prepared = ingestor.prepare(job)
    assert prepared is not None
    documents_service.update_permissions(
        app_engine, admin, document_id, classification=Classification.RESTRICTED, allowed_roles=[Role.ADMIN], allowed_user_ids=[member]
    )
    ingestor.persist(prepared)
    doc = _document(owner_engine, document_id)
    for chunk in _chunks(owner_engine, document_id):
        assert (chunk.classification, chunk.allowed_roles, chunk.acl_version) == ("restricted", ["admin"], doc.acl_version)


def test_documents_deleted_before_processing_are_skipped(
    owner_engine: Engine, app_engine: Engine, seeded: SeededTenant, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path)
    admin = _admin(seeded)
    document_id = _upload(app_engine, storage, admin, "a.txt", b"short lived")
    documents_service.delete_document(app_engine, storage, admin, document_id)
    assert Ingestor(app_engine, storage, FakeEmbedder(), max_pdf_pages=50).run_once() is True
    assert _chunks(owner_engine, document_id) == []
```

- [ ] **Step 2: Run to verify failure** — FAIL (`No module named 'app.ingestion.pipeline'`).

- [ ] **Step 3: Implement**

```python file=backend/app/ingestion/pipeline.py
import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import Connection, Engine, delete, func, insert, select, text, update

from app.db.schema import document_chunks, documents, ingestion_jobs
from app.db.tenancy import tenant_transaction
from app.documents.storage import LocalStorage
from app.documents.validation import KIND_BY_MIME
from app.ingestion.chunker import chunk_text
from app.ingestion.extract import ExtractionError, extract_text
from app.retrieval.embeddings import Embedder

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
STALE_AFTER = timedelta(minutes=10)
RETRY_BACKOFF = timedelta(seconds=30)


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    job_id: UUID
    tenant_id: UUID
    document_id: UUID
    attempts: int


@dataclass(frozen=True, slots=True)
class PreparedDocument:
    job: ClaimedJob
    chunks: list[str]
    embeddings: list[list[float]]


def claim_job(engine: Engine) -> ClaimedJob | None:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT * FROM ingestion_claim_job(:stale)"), {"stale": STALE_AFTER}
        ).one_or_none()
    if row is None:
        return None
    return ClaimedJob(row.job_id, row.tenant_id, row.document_id, row.attempts)


def _finish_job(conn: Connection, job: ClaimedJob, status: str, error: str | None = None) -> None:
    conn.execute(
        update(ingestion_jobs)
        .where(ingestion_jobs.c.id == job.job_id)
        .values(status=status, last_error=error, locked_at=None, updated_at=func.now())
    )


class Ingestor:
    def __init__(
        self, engine: Engine, storage: LocalStorage, embedder: Embedder, *, max_pdf_pages: int
    ) -> None:
        self.engine = engine
        self.storage = storage
        self.embedder = embedder
        self.max_pdf_pages = max_pdf_pages

    def prepare(self, job: ClaimedJob) -> PreparedDocument | None:
        """Extract, chunk, and embed outside any long transaction."""
        with tenant_transaction(self.engine, job.tenant_id) as conn:
            doc = conn.execute(
                select(documents.c.storage_key, documents.c.mime_type, documents.c.deleted_at).where(
                    documents.c.id == job.document_id
                )
            ).one_or_none()
            if doc is None or doc.deleted_at is not None:
                _finish_job(conn, job, "done")
                return None
            conn.execute(
                update(documents)
                .where(documents.c.id == job.document_id)
                .values(status="processing", updated_at=func.now())
            )
        data = self.storage.read(doc.storage_key)
        text_content = extract_text(KIND_BY_MIME[doc.mime_type], data, max_pdf_pages=self.max_pdf_pages)
        pieces = chunk_text(text_content)
        if not pieces:
            raise ExtractionError("no extractable text found")
        return PreparedDocument(job, pieces, self.embedder.embed_documents(pieces))

    def persist(self, prepared: PreparedDocument) -> None:
        """Copy the document's ACL onto the chunks while holding FOR SHARE on the document,
        so a concurrent permission change either finishes first (and we copy the new ACL) or
        waits and then rewrites these chunks itself."""
        job = prepared.job
        with tenant_transaction(self.engine, job.tenant_id) as conn:
            doc = conn.execute(
                select(
                    documents.c.classification,
                    documents.c.allowed_roles,
                    documents.c.allowed_user_ids,
                    documents.c.acl_version,
                    documents.c.deleted_at,
                )
                .where(documents.c.id == job.document_id)
                .with_for_update(read=True)
            ).one_or_none()
            if doc is None or doc.deleted_at is not None:
                _finish_job(conn, job, "done")
                return
            conn.execute(delete(document_chunks).where(document_chunks.c.document_id == job.document_id))
            conn.execute(
                insert(document_chunks),
                [
                    {
                        "tenant_id": job.tenant_id,
                        "document_id": job.document_id,
                        "chunk_index": index,
                        "content": content,
                        "embedding": embedding,
                        "classification": doc.classification,
                        "allowed_roles": doc.allowed_roles,
                        "allowed_user_ids": doc.allowed_user_ids,
                        "acl_version": doc.acl_version,
                    }
                    for index, (content, embedding) in enumerate(
                        zip(prepared.chunks, prepared.embeddings, strict=True)
                    )
                ],
            )
            conn.execute(
                update(documents)
                .where(documents.c.id == job.document_id)
                .values(status="ready", error=None, updated_at=func.now())
            )
            _finish_job(conn, job, "done")

    def fail(self, job: ClaimedJob, error: Exception) -> None:
        permanent = isinstance(error, ExtractionError)
        message = str(error) if permanent else "ingestion failed"
        with tenant_transaction(self.engine, job.tenant_id) as conn:
            if permanent or job.attempts >= MAX_ATTEMPTS:
                _finish_job(conn, job, "failed", message)
                conn.execute(
                    update(documents)
                    .where(documents.c.id == job.document_id)
                    .values(status="failed", error=message, updated_at=func.now())
                )
            else:
                conn.execute(
                    update(ingestion_jobs)
                    .where(ingestion_jobs.c.id == job.job_id)
                    .values(
                        status="queued",
                        last_error=message,
                        locked_at=None,
                        run_after=func.now() + RETRY_BACKOFF * job.attempts,
                        updated_at=func.now(),
                    )
                )
                conn.execute(
                    update(documents)
                    .where(documents.c.id == job.document_id)
                    .values(status="pending", updated_at=func.now())
                )

    def run_once(self) -> bool:
        job = claim_job(self.engine)
        if job is None:
            return False
        try:
            prepared = self.prepare(job)
            if prepared is not None:
                self.persist(prepared)
        except Exception as exc:
            logger.warning(
                "ingestion attempt %d failed for document %s: %s",
                job.attempts,
                job.document_id,
                type(exc).__name__,
            )
            self.fail(job, exc)
        return True
```

```python file=backend/app/worker.py
"""Ingestion worker: python -m app.worker"""

import logging
import signal
import threading
from types import FrameType

from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.db.engine import create_db_engine
from app.documents.storage import LocalStorage
from app.ingestion.pipeline import Ingestor
from app.retrieval.embeddings import FastEmbedEmbedder

logger = logging.getLogger("veyl.worker")


def main() -> int:
    settings = Settings()
    logging.basicConfig(level=settings.log_level.upper())
    engine = create_db_engine(settings.database_url.get_secret_value())
    embedder = FastEmbedEmbedder(settings.embedding_model, settings.embedding_cache_dir)
    ingestor = Ingestor(
        engine,
        LocalStorage(settings.upload_dir),
        embedder,
        max_pdf_pages=settings.max_pdf_pages,
    )
    stop = threading.Event()

    def _stop(signum: int, frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    logger.info("worker started (model %s)", embedder.model_name)
    while not stop.is_set():
        try:
            worked = ingestor.run_once()
        except OperationalError:
            logger.warning("database unavailable; retrying")
            worked = False
        if not worked:
            stop.wait(settings.worker_poll_seconds)
    engine.dispose()
    logger.info("worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run full suite + lint + types** — Expected: PASS.

- [ ] **Step 5: README (worker), devlog, commit, push**

```bash
git add -A
git commit -m "feat(ingestion): worker that claims jobs, chunks, embeds, and copies ACLs safely"
git push origin main
```

---

### Task 7: Permission-filtered retrieval and the retrieval cache

**Files:**
- Create: `backend/app/retrieval/search.py`, `backend/tests/security/test_retrieval_security.py`
- Modify: `README.md`, `docs/devlog.md`

**Interfaces:**
- Consumes: `chunk_read_filter`, `CHUNK_JOIN`, `can_read_chunk`, `Embedder`.
- Produces: `app.retrieval.search`: `RetrievedChunk(chunk_id, document_id, document_title, chunk_index, content, score, acl)`, `AuthorizationMismatch` (500 `authorization_mismatch`), `cache_key(ctx, policy_version, model_name, query) -> bytes`, `retrieve(engine, embedder, ctx, query, *, top_k, min_score=0.0, cache_ttl=None) -> list[RetrievedChunk]`.

- [ ] **Step 1: Write failing tests**

```python file=backend/tests/security/test_retrieval_security.py
"""Brief tests 1 (tenant isolation), 2 (role restrictions), 6 (permission changes),
7 (cache isolation); T14 (fail closed); plus the Python re-check as a second layer."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, insert, select, update

from app.authz import AuthContext, Classification, Role
from app.db.schema import document_chunks, documents, retrieval_cache, tenants
from app.documents import service as documents_service
from app.retrieval import search as search_module
from app.retrieval.search import AuthorizationMismatch, RetrievedChunk, cache_key, retrieve
from tests.factories import add_chunks, create_document, create_tenant_with_admin, create_user
from tests.fakes import FakeEmbedder

pytestmark = pytest.mark.integration

TTL = timedelta(minutes=10)


def _ctx(user_id: UUID, tenant_id: UUID, role: Role) -> AuthContext:
    return AuthContext(user_id=user_id, tenant_id=tenant_id, role=role, session_id=uuid4())


def _doc(owner_engine: Engine, embedder: FakeEmbedder, tenant_id: UUID, uploader: UUID, text: str, **acl: object) -> tuple[UUID, list[UUID]]:
    document_id = create_document(owner_engine, tenant_id, uploader, title=text[:40], **acl)  # type: ignore[arg-type]
    return document_id, add_chunks(owner_engine, document_id, [text], embedder.embed_documents([text]))


def _ids(results: list[RetrievedChunk]) -> set[UUID]:
    return {r.chunk_id for r in results}


def _search(app_engine: Engine, embedder: FakeEmbedder, ctx: AuthContext, query: str, *, top_k: int = 10, cache: bool = False) -> list[RetrievedChunk]:
    return retrieve(app_engine, embedder, ctx, query, top_k=top_k, cache_ttl=TTL if cache else None)


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


def test_tenant_a_cannot_retrieve_tenant_b_documents(owner_engine: Engine, app_engine: Engine, embedder: FakeEmbedder) -> None:
    a = create_tenant_with_admin(owner_engine)
    b = create_tenant_with_admin(owner_engine)
    _, b_chunks = _doc(owner_engine, embedder, b.tenant_id, b.admin_id, "Project Falcon merger terms", allowed_roles=list(Role))
    _, a_chunks = _doc(owner_engine, embedder, a.tenant_id, a.admin_id, "Project Falcon cafeteria menu", allowed_roles=list(Role))
    results = _search(app_engine, embedder, _ctx(a.admin_id, a.tenant_id, Role.ADMIN), "Project Falcon merger terms")
    assert _ids(results) == set(a_chunks)
    assert not _ids(results) & set(b_chunks)


def test_member_cannot_retrieve_admin_only_chunks(owner_engine: Engine, app_engine: Engine, embedder: FakeEmbedder) -> None:
    t = create_tenant_with_admin(owner_engine)
    member, _ = create_user(owner_engine, t.tenant_id, role=Role.MEMBER)
    _, secret = _doc(owner_engine, embedder, t.tenant_id, t.admin_id, "board compensation figures", classification=Classification.RESTRICTED, allowed_roles=[Role.ADMIN])
    _, handbook = _doc(owner_engine, embedder, t.tenant_id, t.admin_id, "employee handbook compensation basics", allowed_roles=[Role.MEMBER])
    results = _search(app_engine, embedder, _ctx(member, t.tenant_id, Role.MEMBER), "board compensation figures")
    assert _ids(results) == set(handbook)
    admin_results = _search(app_engine, embedder, _ctx(t.admin_id, t.tenant_id, Role.ADMIN), "board compensation figures")
    assert set(secret) <= _ids(admin_results)


def test_clearance_caps_explicit_grants(owner_engine: Engine, app_engine: Engine, embedder: FakeEmbedder) -> None:
    t = create_tenant_with_admin(owner_engine)
    member, _ = create_user(owner_engine, t.tenant_id, role=Role.MEMBER)
    manager, _ = create_user(owner_engine, t.tenant_id, role=Role.MANAGER)
    _, confidential = _doc(owner_engine, embedder, t.tenant_id, t.admin_id, "salary review notes", classification=Classification.CONFIDENTIAL, allowed_user_ids=[member, manager])
    assert _search(app_engine, embedder, _ctx(member, t.tenant_id, Role.MEMBER), "salary review notes") == []
    assert _ids(_search(app_engine, embedder, _ctx(manager, t.tenant_id, Role.MANAGER), "salary review notes")) == set(confidential)


def test_permission_change_applies_to_the_next_retrieval(owner_engine: Engine, app_engine: Engine, embedder: FakeEmbedder) -> None:
    t = create_tenant_with_admin(owner_engine)
    member, _ = create_user(owner_engine, t.tenant_id, role=Role.MEMBER)
    admin = _ctx(t.admin_id, t.tenant_id, Role.ADMIN)
    member_ctx = _ctx(member, t.tenant_id, Role.MEMBER)
    document_id, chunks = _doc(owner_engine, embedder, t.tenant_id, t.admin_id, "roadmap milestones", allowed_roles=[Role.MEMBER])
    assert _ids(_search(app_engine, embedder, member_ctx, "roadmap milestones")) == set(chunks)
    documents_service.update_permissions(app_engine, admin, document_id, classification=Classification.INTERNAL, allowed_roles=[Role.ADMIN], allowed_user_ids=[])
    assert _search(app_engine, embedder, member_ctx, "roadmap milestones") == []
    documents_service.update_permissions(app_engine, admin, document_id, classification=Classification.INTERNAL, allowed_roles=[Role.MEMBER], allowed_user_ids=[])
    assert _ids(_search(app_engine, embedder, member_ctx, "roadmap milestones")) == set(chunks)


def test_stale_chunks_are_never_returned(owner_engine: Engine, app_engine: Engine, embedder: FakeEmbedder) -> None:
    t = create_tenant_with_admin(owner_engine)
    document_id, _ = _doc(owner_engine, embedder, t.tenant_id, t.admin_id, "pricing sheet")
    with owner_engine.begin() as conn:
        conn.execute(update(documents).where(documents.c.id == document_id).values(acl_version=documents.c.acl_version + 1))
    assert _search(app_engine, embedder, _ctx(t.admin_id, t.tenant_id, Role.ADMIN), "pricing sheet") == []


def test_identical_query_is_served_from_cache(owner_engine: Engine, app_engine: Engine, embedder: FakeEmbedder) -> None:
    t = create_tenant_with_admin(owner_engine)
    _, chunks = _doc(owner_engine, embedder, t.tenant_id, t.admin_id, "travel policy")
    ctx = _ctx(t.admin_id, t.tenant_id, Role.ADMIN)
    first = _search(app_engine, embedder, ctx, "travel policy", cache=True)
    calls = embedder.query_calls
    second = _search(app_engine, embedder, ctx, "  Travel   POLICY ", cache=True)
    assert embedder.query_calls == calls
    assert _ids(first) == _ids(second) == set(chunks)


def test_cached_results_do_not_leak_to_a_less_privileged_user(owner_engine: Engine, app_engine: Engine, embedder: FakeEmbedder) -> None:
    t = create_tenant_with_admin(owner_engine)
    member, _ = create_user(owner_engine, t.tenant_id, role=Role.MEMBER)
    _, secret = _doc(owner_engine, embedder, t.tenant_id, t.admin_id, "acquisition shortlist", classification=Classification.RESTRICTED, allowed_roles=[Role.ADMIN])
    assert _ids(_search(app_engine, embedder, _ctx(t.admin_id, t.tenant_id, Role.ADMIN), "acquisition shortlist", cache=True)) == set(secret)
    assert _search(app_engine, embedder, _ctx(member, t.tenant_id, Role.MEMBER), "acquisition shortlist", cache=True) == []


def test_forged_cache_entry_is_filtered_on_hit(owner_engine: Engine, app_engine: Engine, embedder: FakeEmbedder) -> None:
    t = create_tenant_with_admin(owner_engine)
    member, _ = create_user(owner_engine, t.tenant_id, role=Role.MEMBER)
    member_ctx = _ctx(member, t.tenant_id, Role.MEMBER)
    _, secret = _doc(owner_engine, embedder, t.tenant_id, t.admin_id, "layoff plan", classification=Classification.RESTRICTED, allowed_roles=[Role.ADMIN])
    with owner_engine.begin() as conn:
        version = conn.execute(select(tenants.c.policy_version).where(tenants.c.id == t.tenant_id)).scalar_one()
        conn.execute(
            insert(retrieval_cache).values(
                cache_key=cache_key(member_ctx, version, embedder.model_name, "layoff plan"),
                tenant_id=t.tenant_id,
                user_id=member,
                policy_version=version,
                chunk_ids=secret,
                scores=[0.99] * len(secret),
                expires_at=func.now() + TTL,
            )
        )
    calls = embedder.query_calls
    assert _search(app_engine, embedder, member_ctx, "layoff plan", cache=True) == []
    assert embedder.query_calls == calls  # the forged row was the cache hit, and it was filtered


def test_permission_change_invalidates_cached_results(owner_engine: Engine, app_engine: Engine, embedder: FakeEmbedder) -> None:
    t = create_tenant_with_admin(owner_engine)
    member, _ = create_user(owner_engine, t.tenant_id, role=Role.MEMBER)
    member_ctx = _ctx(member, t.tenant_id, Role.MEMBER)
    document_id, chunks = _doc(owner_engine, embedder, t.tenant_id, t.admin_id, "bonus schedule", allowed_roles=[Role.MEMBER])
    assert _ids(_search(app_engine, embedder, member_ctx, "bonus schedule", cache=True)) == set(chunks)
    documents_service.update_permissions(app_engine, _ctx(t.admin_id, t.tenant_id, Role.ADMIN), document_id, classification=Classification.INTERNAL, allowed_roles=[], allowed_user_ids=[])
    assert _search(app_engine, embedder, member_ctx, "bonus schedule", cache=True) == []


def test_python_recheck_fails_closed_if_the_sql_filter_is_wrong(
    owner_engine: Engine, app_engine: Engine, embedder: FakeEmbedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = create_tenant_with_admin(owner_engine)
    member, _ = create_user(owner_engine, t.tenant_id, role=Role.MEMBER)
    _doc(owner_engine, embedder, t.tenant_id, t.admin_id, "audit findings", classification=Classification.RESTRICTED, allowed_roles=[Role.ADMIN])
    monkeypatch.setattr(search_module, "chunk_read_filter", lambda ctx: document_chunks.c.tenant_id == ctx.tenant_id)
    with pytest.raises(AuthorizationMismatch):
        _search(app_engine, embedder, _ctx(member, t.tenant_id, Role.MEMBER), "audit findings")


def test_embedding_failure_fails_closed_without_fallback(owner_engine: Engine, app_engine: Engine) -> None:
    t = create_tenant_with_admin(owner_engine)
    _doc(owner_engine, FakeEmbedder(), t.tenant_id, t.admin_id, "anything at all")
    with pytest.raises(RuntimeError):
        _search(app_engine, FakeEmbedder(fail=True), _ctx(t.admin_id, t.tenant_id, Role.ADMIN), "anything at all", cache=True)
    with owner_engine.connect() as conn:
        rows = conn.execute(select(func.count()).select_from(retrieval_cache).where(retrieval_cache.c.tenant_id == t.tenant_id)).scalar_one()
    assert rows == 0


def test_small_tenant_keeps_recall_next_to_a_large_tenant(owner_engine: Engine, app_engine: Engine, embedder: FakeEmbedder) -> None:
    big = create_tenant_with_admin(owner_engine)
    small = create_tenant_with_admin(owner_engine)
    texts = [f"quarterly logistics report {i}" for i in range(300)]
    document_id = create_document(owner_engine, big.tenant_id, big.admin_id)
    add_chunks(owner_engine, document_id, texts, embedder.embed_documents(texts))
    _, mine = _doc(owner_engine, embedder, small.tenant_id, small.admin_id, "quarterly logistics report")
    results = _search(app_engine, embedder, _ctx(small.admin_id, small.tenant_id, Role.ADMIN), "quarterly logistics report", top_k=3)
    assert _ids(results) == set(mine)


def test_results_are_ordered_by_score(owner_engine: Engine, app_engine: Engine, embedder: FakeEmbedder) -> None:
    t = create_tenant_with_admin(owner_engine)
    _doc(owner_engine, embedder, t.tenant_id, t.admin_id, "vacation policy days")
    _doc(owner_engine, embedder, t.tenant_id, t.admin_id, "vacation")
    results = _search(app_engine, embedder, _ctx(t.admin_id, t.tenant_id, Role.ADMIN), "vacation policy days")
    assert [r.score for r in results] == sorted((r.score for r in results), reverse=True)
    assert results[0].content == "vacation policy days"
```

- [ ] **Step 2: Run to verify failure** — FAIL (`No module named 'app.retrieval.search'`).

- [ ] **Step 3: Implement**

```python file=backend/app/retrieval/search.py
"""Permission-filtered retrieval (spec sections 6.2-6.3).

One tenant transaction reads policy_version, the cache, and the vector search, so a cache
key can never be computed against one policy and used against another. The cache stores
chunk ids only; on a hit they are re-fetched through the same SQL filter. Every returned
row is then re-checked with the Python policy, and any disagreement fails the request."""

import hashlib
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.authz import AuthContext, ChunkAcl, Classification, Role, can_read_chunk
from app.authz.sql import CHUNK_JOIN, chunk_read_filter
from app.db.schema import document_chunks, documents, retrieval_cache, tenants
from app.db.tenancy import tenant_transaction
from app.errors import AppError
from app.retrieval.embeddings import Embedder

logger = logging.getLogger(__name__)


class AuthorizationMismatch(AppError):
    status_code = 500
    code = "authorization_mismatch"
    default_message = "An internal authorization check failed."


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: UUID
    document_id: UUID
    document_title: str
    chunk_index: int
    content: str
    score: float
    acl: ChunkAcl


_COLUMNS = (
    document_chunks.c.id,
    document_chunks.c.document_id,
    documents.c.title,
    document_chunks.c.chunk_index,
    document_chunks.c.content,
    document_chunks.c.tenant_id,
    document_chunks.c.classification,
    document_chunks.c.allowed_roles,
    document_chunks.c.allowed_user_ids,
)


def normalise_query(query: str) -> str:
    return " ".join(query.lower().split())


def cache_key(ctx: AuthContext, policy_version: int, model_name: str, query: str) -> bytes:
    material = f"{ctx.tenant_id}|{ctx.user_id}|{policy_version}|{model_name}|{normalise_query(query)}"
    return hashlib.sha256(material.encode("utf-8")).digest()


def _result(row: Any, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=row.id,
        document_id=row.document_id,
        document_title=row.title,
        chunk_index=row.chunk_index,
        content=row.content,
        score=score,
        acl=ChunkAcl(
            tenant_id=row.tenant_id,
            allowed_roles=frozenset(Role(r) for r in row.allowed_roles),
            allowed_user_ids=frozenset(row.allowed_user_ids),
            classification=Classification(row.classification),
        ),
    )


def _cached(conn: Connection, ctx: AuthContext, key: bytes, policy_version: int) -> list[tuple[UUID, float]] | None:
    row = conn.execute(
        select(retrieval_cache.c.chunk_ids, retrieval_cache.c.scores).where(
            retrieval_cache.c.cache_key == key,
            retrieval_cache.c.user_id == ctx.user_id,
            retrieval_cache.c.policy_version == policy_version,
            retrieval_cache.c.expires_at > func.now(),
        )
    ).one_or_none()
    if row is None:
        return None
    return list(zip(row.chunk_ids, row.scores, strict=True))


def _fetch_by_ids(conn: Connection, ctx: AuthContext, entries: list[tuple[UUID, float]]) -> list[RetrievedChunk]:
    if not entries:
        return []
    scores = dict(entries)
    rows = conn.execute(
        select(*_COLUMNS)
        .select_from(CHUNK_JOIN)
        .where(chunk_read_filter(ctx), document_chunks.c.id.in_(list(scores)))
    ).all()
    return [_result(row, scores[row.id]) for row in rows]


def _vector_search(conn: Connection, ctx: AuthContext, vector: list[float], top_k: int) -> list[RetrievedChunk]:
    distance = document_chunks.c.embedding.cosine_distance(vector)
    rows = conn.execute(
        select(*_COLUMNS, distance.label("distance"))
        .select_from(CHUNK_JOIN)
        .where(chunk_read_filter(ctx))
        .order_by(distance)
        .limit(top_k)
    ).all()
    return [_result(row, 1.0 - float(row.distance)) for row in rows]


def _store(conn: Connection, ctx: AuthContext, key: bytes, policy_version: int, results: list[RetrievedChunk], ttl: timedelta) -> None:
    conn.execute(delete(retrieval_cache).where(retrieval_cache.c.expires_at < func.now()))
    statement = pg_insert(retrieval_cache).values(
        cache_key=key,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        policy_version=policy_version,
        chunk_ids=[r.chunk_id for r in results],
        scores=[r.score for r in results],
        expires_at=func.now() + ttl,
    )
    conn.execute(
        statement.on_conflict_do_update(
            index_elements=[retrieval_cache.c.cache_key],
            set_={
                "policy_version": statement.excluded.policy_version,
                "chunk_ids": statement.excluded.chunk_ids,
                "scores": statement.excluded.scores,
                "created_at": func.now(),
                "expires_at": statement.excluded.expires_at,
            },
        )
    )


def retrieve(
    engine: Engine,
    embedder: Embedder,
    ctx: AuthContext,
    query: str,
    *,
    top_k: int,
    min_score: float = 0.0,
    cache_ttl: timedelta | None = None,
) -> list[RetrievedChunk]:
    with tenant_transaction(engine, ctx.tenant_id) as conn:
        # RLS filters after the HNSW scan; iterative scans keep top-k recall for small tenants.
        conn.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        policy_version = int(
            conn.execute(select(tenants.c.policy_version).where(tenants.c.id == ctx.tenant_id)).scalar_one()
        )
        key = cache_key(ctx, policy_version, embedder.model_name, query)
        entries = _cached(conn, ctx, key, policy_version) if cache_ttl else None
        if entries is not None:
            results = _fetch_by_ids(conn, ctx, entries)
        else:
            vector = embedder.embed_query(query)
            results = [r for r in _vector_search(conn, ctx, vector, top_k) if r.score >= min_score]
            if cache_ttl:
                _store(conn, ctx, key, policy_version, results, cache_ttl)

    for result in results:
        if not can_read_chunk(ctx, result.acl):
            logger.error("authorization mismatch: SQL filter returned chunk %s for user %s", result.chunk_id, ctx.user_id)
            raise AuthorizationMismatch()
    return sorted(results, key=lambda r: r.score, reverse=True)
```

- [ ] **Step 4: Run full suite + lint + types** — Expected: PASS.

- [ ] **Step 5: README (retrieval guarantees), devlog, commit, push**

```bash
git add -A
git commit -m "feat(retrieval): permission-filtered vector search with safe cache and re-check"
git push origin main
```

---

### Task 8: Containers, end-to-end ingestion smoke, documentation, review

**Files:**
- Modify: `backend/Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml`, `.env.example`, `README.md`, `docs/devlog.md`, `docs/decisions.md`, `docs/superpowers/specs/2026-10-15-veyl-design.md`
- Create: `docker/smoke/ingest.sh`

- [ ] **Step 1: Backend image** — after `RUN uv sync ...` and before `COPY . .` add:

```dockerfile
ENV EMBEDDING_CACHE_DIR=/opt/models
# Bake the embedding model into the image: no download at runtime, works offline.
RUN uv run --no-sync python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/opt/models')"
```
and before `USER veyl`: `RUN mkdir -p /data/uploads && chown veyl /data/uploads`.

- [ ] **Step 2: Compose** — add `UPLOAD_DIR: /data/uploads` and `MAX_UPLOAD_MB: ${MAX_UPLOAD_MB:-10}` to `api`, mount `uploads:/data/uploads`, and add:

```yaml
  worker:
    build: ./backend
    command: ["python", "-m", "app.worker"]
    environment:
      DATABASE_URL: postgresql+psycopg://veyl_app:${VEYL_APP_PASSWORD}@db:5432/veyl
      UPLOAD_DIR: /data/uploads
      LOG_LEVEL: ${LOG_LEVEL:-info}
    volumes:
      - uploads:/data/uploads
    depends_on:
      migrate:
        condition: service_completed_successfully
    restart: unless-stopped
```
plus `uploads:` under `volumes:`. Add `MAX_UPLOAD_MB=10` to `.env.example`.

- [ ] **Step 3: Smoke script**

```sh file=docker/smoke/ingest.sh
#!/bin/sh
# Uploads a text document through the public entry point and waits until the worker has
# ingested it. Expects a cookie jar from a logged-in admin.
# Usage: ingest.sh <base-url> <cookie-jar>
set -eu
BASE=$1
JAR=$2
CSRF=$(awk '$6 == "veyl_csrf" { print $7 }' "$JAR")
printf 'Travel policy\n\nEmployees may book economy flights.\n' > /tmp/veyl-smoke.txt
ID=$(curl -fsS -b "$JAR" -H "X-CSRF-Token: $CSRF" \
  -F "file=@/tmp/veyl-smoke.txt;filename=travel.txt" -F "title=Travel policy" \
  -F "classification=internal" -F "allowed_roles=member" \
  "$BASE/api/v1/documents" | sed -n 's/.*"id":"\([^"]*\)".*/\1/p')
[ -n "$ID" ] || { echo "FAIL: upload returned no id"; exit 1; }
i=0
while [ $i -lt 60 ]; do
  STATUS=$(curl -fsS -b "$JAR" "$BASE/api/v1/documents/$ID" | sed -n 's/.*"status":"\([a-z]*\)".*/\1/p')
  [ "$STATUS" = "ready" ] && { echo "OK: document $ID ingested"; exit 0; }
  [ "$STATUS" = "failed" ] && { echo "FAIL: ingestion failed"; exit 1; }
  i=$((i + 1))
  sleep 2
done
echo "FAIL: document not ready after 120s (last status: $STATUS)"
exit 1
```

- [ ] **Step 4: CI** — in `compose-smoke`, after "Log in through the web proxy" add:

```yaml
      - name: Upload and ingest a document
        run: sh docker/smoke/ingest.sh http://127.0.0.1:3000 jar.txt
```

- [ ] **Step 5: Local end-to-end** — `docker compose up -d --build`, provision, log in, run `sh docker/smoke/ingest.sh http://127.0.0.1:3000 <jar>`; expect `OK`. Then `docker compose down`.

- [ ] **Step 6: Docs** — decisions D-024 (`text[]` + CHECK for roles), D-025 (no query endpoint until Phase 4), D-026 (model baked into image), D-027 (worker splits prepare/persist; FOR SHARE on persist), D-028 (iterative scans + Python sort), D-029 (FOR NO KEY UPDATE for membership lock). Spec: §5.2/5.3 roles column type, §6.1 worker split, §13 Phase 3 done. README: Phase 3 Done, Phase 4 Next, documents API table, ingestion + retrieval sections, services table (worker). Devlog: Phase 3 summary.

- [ ] **Step 7: Commit, push, watch CI** — Expected: backend, frontend, compose-smoke green (compose-smoke now ingests a real document with the real model).

- [ ] **Step 8: Final whole-phase review** — fresh reviewer, Review Focus above; fix Critical/Important RED-to-GREEN; minors to the devlog.
