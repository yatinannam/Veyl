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
    # See colleagues' names and roles in order to share documents with them.
    USER_DIRECTORY = "user.directory"


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
    if action is Action.USER_DIRECTORY:
        return ctx.role in _UPLOADERS
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
        granted |= {Action.DOCUMENT_UPLOAD, Action.USER_DIRECTORY}
    return sorted(str(action) for action in granted)


def can_read_chunk(ctx: AuthContext, acl: ChunkAcl) -> bool:
    """The chunk read rule (spec section 4.3), minus the document status/version checks
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
