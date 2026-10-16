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
