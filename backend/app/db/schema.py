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
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, ENUM, JSONB, UUID

from app.authz.roles import Classification, Role

EMBEDDING_DIM = 384

metadata = MetaData()

user_role = ENUM(*[r.value for r in Role], name="user_role", create_type=False)
classification = ENUM(*[c.value for c in Classification], name="classification", create_type=False)
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
    Column("audit_query_mode", Text, nullable=False, server_default=text("'hash'")),
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

msg_role = ENUM("user", "assistant", name="msg_role", create_type=False)
audit_decision = ENUM("allow", "deny", "error", name="audit_decision", create_type=False)

conversations = Table(
    "conversations",
    metadata,
    _id(),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("title", Text, nullable=False),
    _created_at(),
    _updated_at(),
    UniqueConstraint("tenant_id", "id"),
    ForeignKeyConstraint(
        ["tenant_id", "user_id"], ["users.tenant_id", "users.id"], ondelete="CASCADE"
    ),
)

messages = Table(
    "messages",
    metadata,
    _id(),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("conversation_id", UUID(as_uuid=True), nullable=False),
    Column("role", msg_role, nullable=False),
    Column("content", Text, nullable=False),
    Column(
        "cited_chunk_ids", ARRAY(UUID(as_uuid=True)), nullable=False, server_default=text("'{}'")
    ),
    Column(
        "cited_document_ids",
        ARRAY(UUID(as_uuid=True)),
        nullable=False,
        server_default=text("'{}'"),
    ),
    Column("trace_id", UUID(as_uuid=True), nullable=False),
    Column(
        "source_chunk_ids",
        ARRAY(UUID(as_uuid=True)),
        nullable=False,
        server_default=text("'{}'"),
    ),
    _created_at(),
    ForeignKeyConstraint(
        ["tenant_id", "conversation_id"],
        ["conversations.tenant_id", "conversations.id"],
        ondelete="CASCADE",
    ),
)

audit_events = Table(
    "audit_events",
    metadata,
    _id(),
    Column("tenant_id", UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False),
    Column(
        "occurred_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
    ),
    Column("actor_user_id", UUID(as_uuid=True), nullable=True),
    Column("event_type", Text, nullable=False),
    Column("decision", audit_decision, nullable=False, server_default=text("'allow'")),
    Column("query_ref", Text, nullable=True),
    Column("query_text", Text, nullable=True),
    Column(
        "retrieved_chunk_ids",
        ARRAY(UUID(as_uuid=True)),
        nullable=False,
        server_default=text("'{}'"),
    ),
    Column(
        "retrieved_document_ids",
        ARRAY(UUID(as_uuid=True)),
        nullable=False,
        server_default=text("'{}'"),
    ),
    Column("result_count", Integer, nullable=True),
    Column("trace_id", UUID(as_uuid=True), nullable=True),
    Column("model", Text, nullable=True),
    Column("details", JSONB, nullable=False, server_default=text("'{}'")),
)
