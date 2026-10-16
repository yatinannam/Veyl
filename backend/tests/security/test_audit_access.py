"""Brief test 8 (users cannot access audit logs outside their permitted scope) and T8."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, select

from app.audit import service as audit
from app.authz import Role
from app.db.schema import audit_events
from app.db.tenancy import tenant_transaction
from tests.api import API, client_for, login
from tests.conftest import DatabaseUrls
from tests.factories import SeededTenant, create_tenant_with_admin, create_user

pytestmark = pytest.mark.integration


def _seed(owner_engine: Engine, tenant: SeededTenant, n: int, **fields: object) -> None:
    with tenant_transaction(owner_engine, tenant.tenant_id) as conn:
        for i in range(n):
            audit.record(
                conn,
                tenant_id=tenant.tenant_id,
                event_type=str(fields.get("event_type", "rag.query")),
                actor_user_id=fields.get("actor", tenant.admin_id),  # type: ignore[arg-type]
                document_ids=fields.get("document_ids", ()),  # type: ignore[arg-type]
                details={"marker": str(fields.get("marker", "")), "i": i},
            )


@pytest.mark.parametrize("role", [Role.MEMBER, Role.MANAGER])
def test_non_admins_cannot_read_the_audit_log(
    owner_engine: Engine, database_urls: DatabaseUrls, role: Role
) -> None:
    t = create_tenant_with_admin(owner_engine)
    _, email = create_user(owner_engine, t.tenant_id, role=role)
    with client_for(database_urls.app) as client:
        login(client, t.slug, email)
        r = client.get(f"{API}/audit-events")
    assert (r.status_code, r.json()["error"]["code"]) == (403, "forbidden")


def test_admins_see_only_their_own_tenant(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    a = create_tenant_with_admin(owner_engine)
    b = create_tenant_with_admin(owner_engine)
    _seed(owner_engine, a, 2, marker="tenant-a")
    _seed(owner_engine, b, 2, marker="tenant-b")
    with client_for(database_urls.app) as client:
        login(client, a.slug, a.admin_email)
        r = client.get(f"{API}/audit-events", params={"tenant_id": str(b.tenant_id), "limit": 200})
    markers = {e["details"].get("marker") for e in r.json()["events"]}
    assert "tenant-b" not in markers
    assert "tenant-a" in markers


def test_filters_and_keyset_pagination(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    t = create_tenant_with_admin(owner_engine)
    member, _ = create_user(owner_engine, t.tenant_id)
    document_id = uuid.uuid4()
    _seed(owner_engine, t, 5, marker="paged")
    _seed(
        owner_engine, t, 1, event_type="document.uploaded", actor=member, document_ids=[document_id]
    )
    with client_for(database_urls.app) as client:
        login(client, t.slug, t.admin_email)
        seen: list[str] = []
        cursor = None
        while True:
            params: dict[str, object] = {"event_type": "rag.query", "limit": 2}
            if cursor:
                params["cursor"] = cursor
            page = client.get(f"{API}/audit-events", params=params).json()
            seen += [e["id"] for e in page["events"]]
            cursor = page["next_cursor"]
            if not cursor:
                break
        by_user = client.get(f"{API}/audit-events", params={"user_id": str(member)}).json()[
            "events"
        ]
        by_document = client.get(
            f"{API}/audit-events", params={"document_id": str(document_id)}
        ).json()["events"]
        future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        none_yet = client.get(f"{API}/audit-events", params={"from": future}).json()["events"]
    assert len(seen) == len(set(seen)) == 5
    assert [e["event_type"] for e in by_user] == ["document.uploaded"]
    assert [e["actor_user_id"] for e in by_document] == [str(member)]
    assert none_yet == []


def test_invalid_cursor_is_rejected(owner_engine: Engine, database_urls: DatabaseUrls) -> None:
    t = create_tenant_with_admin(owner_engine)
    with client_for(database_urls.app) as client:
        login(client, t.slug, t.admin_email)
        r = client.get(f"{API}/audit-events", params={"cursor": "not-a-cursor"})
    assert (r.status_code, r.json()["error"]["code"]) == (422, "invalid_cursor")


def test_reading_the_audit_log_is_itself_audited(
    owner_engine: Engine, database_urls: DatabaseUrls
) -> None:
    t = create_tenant_with_admin(owner_engine)
    with client_for(database_urls.app) as client:
        login(client, t.slug, t.admin_email)
        client.get(f"{API}/audit-events", params={"event_type": "rag.query"})
    with owner_engine.connect() as conn:
        viewed = conn.execute(
            select(audit_events.c.actor_user_id, audit_events.c.details).where(
                audit_events.c.tenant_id == t.tenant_id, audit_events.c.event_type == "audit.viewed"
            )
        ).all()
    assert [(row.actor_user_id, row.details["filters"]["event_type"]) for row in viewed] == [
        (t.admin_id, "rag.query")
    ]


def test_unknown_event_types_are_refused(owner_engine: Engine) -> None:
    t = create_tenant_with_admin(owner_engine)
    with pytest.raises(ValueError), tenant_transaction(owner_engine, t.tenant_id) as conn:
        audit.record(conn, tenant_id=t.tenant_id, event_type="made.up")


def test_query_reference_is_keyed_and_normalised() -> None:
    tenant = uuid.uuid4()
    a = audit.query_reference(b"k" * 32, tenant, "What is  Q3 revenue?")
    assert a == audit.query_reference(b"k" * 32, tenant, "what is q3 revenue?")
    assert a != audit.query_reference(b"j" * 32, tenant, "what is q3 revenue?")
    assert a != audit.query_reference(b"k" * 32, uuid.uuid4(), "what is q3 revenue?")
