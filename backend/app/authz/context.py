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
