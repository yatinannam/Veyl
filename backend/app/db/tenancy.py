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
