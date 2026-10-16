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
