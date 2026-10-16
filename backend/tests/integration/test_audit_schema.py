import uuid

import pytest
from sqlalchemy import Engine, delete, insert, select, text, update
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.authz import Role
from app.db.schema import audit_events, conversations, messages, tenants
from app.db.tenancy import tenant_transaction
from tests.factories import create_tenant, create_user

pytestmark = pytest.mark.integration


def _event(owner_engine: Engine, tenant_id: uuid.UUID) -> uuid.UUID:
    with owner_engine.begin() as conn:
        return conn.execute(  # type: ignore[no-any-return]
            insert(audit_events)
            .values(tenant_id=tenant_id, event_type="rag.query")
            .returning(audit_events.c.id)
        ).scalar_one()


def test_app_role_can_only_append_audit_events(owner_engine: Engine, app_engine: Engine) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    event_id = _event(owner_engine, tenant_id)
    with tenant_transaction(app_engine, tenant_id) as conn:
        conn.execute(insert(audit_events).values(tenant_id=tenant_id, event_type="rag.query"))
    with pytest.raises(ProgrammingError), tenant_transaction(app_engine, tenant_id) as conn:
        conn.execute(
            update(audit_events).where(audit_events.c.id == event_id).values(event_type="x.y")
        )
    with pytest.raises(ProgrammingError), tenant_transaction(app_engine, tenant_id) as conn:
        conn.execute(delete(audit_events).where(audit_events.c.id == event_id))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET event_type = 'x.y' WHERE id = :id",
        "DELETE FROM audit_events WHERE id = :id",
        "TRUNCATE audit_events",
    ],
    ids=["update", "delete", "truncate"],
)
def test_even_the_owner_cannot_rewrite_the_audit_log(owner_engine: Engine, statement: str) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    event_id = _event(owner_engine, tenant_id)
    with pytest.raises(ProgrammingError, match="append-only"), owner_engine.begin() as conn:
        conn.execute(text(statement), {"id": event_id})


def test_event_types_are_namespaced(owner_engine: Engine) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        conn.execute(insert(audit_events).values(tenant_id=tenant_id, event_type="DROP TABLE"))


def test_messages_cannot_point_at_another_tenants_conversation(owner_engine: Engine) -> None:
    tenant_a, _ = create_tenant(owner_engine)
    tenant_b, _ = create_tenant(owner_engine)
    user_a, _ = create_user(owner_engine, tenant_a, role=Role.MEMBER)
    with owner_engine.begin() as conn:
        conversation = conn.execute(
            insert(conversations)
            .values(tenant_id=tenant_a, user_id=user_a, title="t")
            .returning(conversations.c.id)
        ).scalar_one()
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        conn.execute(
            insert(messages).values(
                tenant_id=tenant_b,
                conversation_id=conversation,
                role="user",
                content="x",
                trace_id=uuid.uuid4(),
            )
        )


def test_deleting_a_conversation_removes_its_messages(
    owner_engine: Engine, app_engine: Engine
) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    user_id, _ = create_user(owner_engine, tenant_id)
    with tenant_transaction(app_engine, tenant_id) as conn:
        conversation = conn.execute(
            insert(conversations)
            .values(tenant_id=tenant_id, user_id=user_id, title="t")
            .returning(conversations.c.id)
        ).scalar_one()
        conn.execute(
            insert(messages).values(
                tenant_id=tenant_id,
                conversation_id=conversation,
                role="user",
                content="hi",
                trace_id=uuid.uuid4(),
            )
        )
    with tenant_transaction(app_engine, tenant_id) as conn:
        conn.execute(delete(conversations).where(conversations.c.id == conversation))
    with owner_engine.connect() as conn:
        left = conn.execute(
            select(messages.c.id).where(messages.c.conversation_id == conversation)
        ).all()
    assert left == []


def test_audit_query_mode_defaults_to_hash(owner_engine: Engine) -> None:
    tenant_id, _ = create_tenant(owner_engine)
    with owner_engine.connect() as conn:
        mode = conn.execute(
            select(tenants.c.audit_query_mode).where(tenants.c.id == tenant_id)
        ).scalar_one()
    assert mode == "hash"
