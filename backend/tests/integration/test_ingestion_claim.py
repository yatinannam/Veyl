from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text

from app.authz import Role
from tests.factories import create_document, create_job, create_tenant, create_user, drain_jobs

pytestmark = pytest.mark.integration

STALE = timedelta(minutes=10)


def _claim(conn: Connection) -> Any:
    return conn.execute(
        text("SELECT * FROM ingestion_claim_job(:stale)"), {"stale": STALE}
    ).one_or_none()


def _pending_document(owner_engine: Engine) -> tuple[Any, Any]:
    tenant_id, _ = create_tenant(owner_engine)
    user_id, _ = create_user(owner_engine, tenant_id, role=Role.ADMIN)
    return tenant_id, create_document(owner_engine, tenant_id, user_id, status="pending")


def test_claim_returns_a_queued_job_exactly_once(owner_engine: Engine, app_engine: Engine) -> None:
    drain_jobs(owner_engine)
    tenant_id, document_id = _pending_document(owner_engine)
    job_id = create_job(owner_engine, tenant_id, document_id)
    with app_engine.begin() as conn:
        first = _claim(conn)
    with app_engine.begin() as conn:
        second = _claim(conn)
    assert first is not None
    assert (first.job_id, first.tenant_id, first.document_id, first.attempts) == (
        job_id,
        tenant_id,
        document_id,
        1,
    )
    assert second is None


def test_claim_skips_jobs_scheduled_for_later(owner_engine: Engine, app_engine: Engine) -> None:
    drain_jobs(owner_engine)
    tenant_id, document_id = _pending_document(owner_engine)
    create_job(owner_engine, tenant_id, document_id, run_after=timedelta(minutes=5))
    with app_engine.begin() as conn:
        assert _claim(conn) is None


def test_claim_reclaims_stale_running_jobs(owner_engine: Engine, app_engine: Engine) -> None:
    drain_jobs(owner_engine)
    tenant_id, document_id = _pending_document(owner_engine)
    job_id = create_job(
        owner_engine, tenant_id, document_id, status="running", locked_ago=timedelta(hours=1)
    )
    with app_engine.begin() as conn:
        claimed = _claim(conn)
    assert claimed is not None
    assert claimed.job_id == job_id


def test_concurrent_claims_take_different_jobs(owner_engine: Engine, app_engine: Engine) -> None:
    drain_jobs(owner_engine)
    tenant_id, document_id = _pending_document(owner_engine)
    other_tenant, other_document = _pending_document(owner_engine)
    create_job(owner_engine, tenant_id, document_id)
    create_job(owner_engine, other_tenant, other_document)
    with app_engine.connect() as first, app_engine.connect() as second, first.begin():
        a = _claim(first)
        with second.begin():
            second.execute(text("SET LOCAL lock_timeout = '2s'"))
            b = _claim(second)
    assert a is not None and b is not None
    assert a.job_id != b.job_id
