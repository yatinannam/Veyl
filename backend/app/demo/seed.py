"""Seeds the two-company demo. Tenants are created with the owner role, because the API
deliberately has no way to create them; people, documents, and permissions are then
created through the public API as each company's admin, exactly as a person would."""

import time
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Engine, select

from app.db.schema import tenants
from app.demo.content import TENANTS, DemoTenant
from app.demo.http import ApiSession, Multipart, Transport
from app.provisioning import provision_tenant


class AlreadySeeded(Exception):
    pass


class DemoNotReady(Exception):
    pass


@dataclass(frozen=True, slots=True)
class DemoSummary:
    tenants: list[str]
    users: list[str]
    documents: list[str]


def _existing(owner_engine: Engine) -> list[str]:
    slugs = [t.slug for t in TENANTS]
    with owner_engine.connect() as conn:
        return list(conn.execute(select(tenants.c.slug).where(tenants.c.slug.in_(slugs))).scalars())


def _seed_tenant(session: ApiSession, tenant: DemoTenant, password: str) -> None:
    session.login(tenant.slug, tenant.admin.email, password)
    ids = {}
    for person in tenant.people:
        created = session.post(
            "/users", json={"email": person.email, "password": password, "role": person.role}
        )
        ids[person.email] = created["id"]
    for document in tenant.documents:
        fields = [("title", document.title), ("classification", document.classification)]
        fields += [("allowed_roles", role) for role in document.allowed_roles]
        fields += [("allowed_user_ids", ids[email]) for email in document.shared_with]
        session.post(
            "/documents",
            multipart=Multipart(
                fields=fields,
                files=[("file", document.filename, "text/markdown", document.content())],
            ),
        )


def seed_demo(
    owner_engine: Engine, new_transport: Callable[[], Transport], password: str
) -> DemoSummary:
    """Creates Acme and Globex. Refuses (and changes nothing) if either already exists."""
    existing = _existing(owner_engine)
    if existing:
        raise AlreadySeeded(", ".join(sorted(existing)))
    for tenant in TENANTS:
        provision_tenant(
            owner_engine,
            slug=tenant.slug,
            name=tenant.name,
            admin_email=tenant.admin.email,
            admin_password=password,
        )
    for tenant in TENANTS:
        _seed_tenant(ApiSession(new_transport()), tenant, password)
    return DemoSummary(
        tenants=[t.slug for t in TENANTS],
        users=[u.email for t in TENANTS for u in (t.admin, *t.people)],
        documents=[d.title for t in TENANTS for d in t.documents],
    )


def wait_until_ready(
    new_transport: Callable[[], Transport],
    password: str,
    *,
    timeout: float,
    interval: float = 2.0,
) -> None:
    """Waits until the worker has processed every demo document, so the walkthrough works
    as soon as the command returns. Raises DemoNotReady naming what is still missing."""
    sessions = []
    for tenant in TENANTS:
        session = ApiSession(new_transport())
        session.login(tenant.slug, tenant.admin.email, password)
        sessions.append((tenant, session))
    deadline = time.monotonic() + timeout
    while True:
        pending: list[str] = []
        for tenant, session in sessions:
            status = {d["title"]: (d["status"], d["error"]) for d in session.get("/documents")}
            for document in tenant.documents:
                state, error = status.get(document.title, ("missing", None))
                if state == "failed":
                    raise DemoNotReady(f"{document.title} failed to process: {error}")
                if state != "ready":
                    pending.append(document.title)
        if not pending:
            return
        if time.monotonic() >= deadline:
            raise DemoNotReady("still processing: " + ", ".join(pending))
        time.sleep(interval)
