"""SQLAlchemy Core definitions mirroring the hand-written migrations.
`tests/integration/test_schema.py` fails if the two drift apart."""

from typing import Any

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


def _id() -> Column[Any]:
    return Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _created_at() -> Column[Any]:
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
