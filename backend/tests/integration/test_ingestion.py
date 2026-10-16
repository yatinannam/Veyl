from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, select, update

from app.authz import AuthContext, Classification, Role
from app.db.schema import document_chunks, documents, ingestion_jobs
from app.documents import service as documents_service
from app.documents.storage import LocalStorage
from app.documents.validation import validate_upload
from app.ingestion.pipeline import MAX_ATTEMPTS, Ingestor, claim_job
from app.retrieval.search import retrieve
from tests.factories import SeededTenant, create_tenant_with_admin, create_user, drain_jobs
from tests.fakes import FakeEmbedder, encrypt_pdf, make_heavy_pdf, make_pdf

pytestmark = pytest.mark.integration


@pytest.fixture
def seeded(owner_engine: Engine) -> SeededTenant:
    drain_jobs(owner_engine)
    return create_tenant_with_admin(owner_engine)


def _admin(seeded: SeededTenant) -> AuthContext:
    return AuthContext(
        user_id=seeded.admin_id, tenant_id=seeded.tenant_id, role=Role.ADMIN, session_id=uuid4()
    )


def _upload(
    app_engine: Engine,
    storage: LocalStorage,
    ctx: AuthContext,
    filename: str,
    data: bytes,
    **acl: object,
) -> UUID:
    record = documents_service.upload_document(
        app_engine,
        storage,
        ctx,
        upload=validate_upload(filename, data, max_bytes=10_000_000),
        data=data,
        title=filename,
        classification=acl.get("classification", Classification.INTERNAL),  # type: ignore[arg-type]
        allowed_roles=acl.get("roles", [Role.MEMBER]),  # type: ignore[arg-type]
        allowed_user_ids=[],
    )
    return record.id


def _document(owner_engine: Engine, document_id: UUID) -> Any:
    with owner_engine.connect() as conn:
        return conn.execute(select(documents).where(documents.c.id == document_id)).one()


def _chunks(owner_engine: Engine, document_id: UUID) -> list[Any]:
    with owner_engine.connect() as conn:
        return list(
            conn.execute(
                select(document_chunks)
                .where(document_chunks.c.document_id == document_id)
                .order_by(document_chunks.c.chunk_index)
            ).all()
        )


def test_text_document_is_chunked_embedded_and_ready(
    owner_engine: Engine, app_engine: Engine, seeded: SeededTenant, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path)
    text = "\n\n".join(
        f"Section {i}: the travel policy covers flights and hotels." for i in range(40)
    )
    document_id = _upload(app_engine, storage, _admin(seeded), "policy.md", text.encode())
    ingestor = Ingestor(app_engine, storage, FakeEmbedder(), max_pdf_pages=50)
    assert ingestor.run_once() is True
    assert ingestor.run_once() is False
    doc = _document(owner_engine, document_id)
    chunks = _chunks(owner_engine, document_id)
    assert doc.status == "ready"
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.allowed_roles == doc.allowed_roles
        assert chunk.allowed_user_ids == doc.allowed_user_ids
        assert chunk.classification == doc.classification
        assert chunk.acl_version == doc.acl_version
        assert len(chunk.embedding) == 384


def test_pdf_text_reaches_chunks(
    owner_engine: Engine, app_engine: Engine, seeded: SeededTenant, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path)
    document_id = _upload(
        app_engine,
        storage,
        _admin(seeded),
        "q3.pdf",
        make_pdf(["Quarterly revenue grew 12 percent"]),
    )
    Ingestor(app_engine, storage, FakeEmbedder(), max_pdf_pages=50).run_once()
    assert "Quarterly revenue grew 12 percent" in _chunks(owner_engine, document_id)[0].content


def test_encrypted_pdf_fails_permanently_with_a_safe_message(
    owner_engine: Engine, app_engine: Engine, seeded: SeededTenant, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path)
    document_id = _upload(
        app_engine, storage, _admin(seeded), "locked.pdf", encrypt_pdf(make_pdf(["secret"]))
    )
    Ingestor(app_engine, storage, FakeEmbedder(), max_pdf_pages=50).run_once()
    doc = _document(owner_engine, document_id)
    assert (doc.status, doc.error) == ("failed", "encrypted PDFs are not supported")
    assert _chunks(owner_engine, document_id) == []
    with owner_engine.connect() as conn:
        assert (
            conn.execute(
                select(ingestion_jobs.c.status).where(ingestion_jobs.c.document_id == document_id)
            ).scalar_one()
            == "failed"
        )


def test_transient_failures_retry_then_fail(
    owner_engine: Engine, app_engine: Engine, seeded: SeededTenant, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path)
    document_id = _upload(app_engine, storage, _admin(seeded), "a.txt", b"hello world")
    ingestor = Ingestor(app_engine, storage, FakeEmbedder(fail=True), max_pdf_pages=50)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        assert ingestor.run_once() is True
        with owner_engine.begin() as conn:
            job = conn.execute(
                select(ingestion_jobs).where(ingestion_jobs.c.document_id == document_id)
            ).one()
            assert job.attempts == attempt
            conn.execute(
                update(ingestion_jobs)
                .where(ingestion_jobs.c.id == job.id)
                .values(run_after=func.now())
            )
    doc = _document(owner_engine, document_id)
    assert (doc.status, doc.error) == ("failed", "ingestion failed")


def test_permission_change_during_ingestion_reaches_new_chunks(
    owner_engine: Engine, app_engine: Engine, seeded: SeededTenant, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path)
    admin = _admin(seeded)
    member, _ = create_user(owner_engine, seeded.tenant_id, role=Role.MEMBER)
    document_id = _upload(app_engine, storage, admin, "a.txt", b"merger terms draft")
    ingestor = Ingestor(app_engine, storage, FakeEmbedder(), max_pdf_pages=50)
    job = claim_job(app_engine)
    assert job is not None
    prepared = ingestor.prepare(job)
    assert prepared is not None
    documents_service.update_permissions(
        app_engine,
        admin,
        document_id,
        classification=Classification.RESTRICTED,
        allowed_roles=[Role.ADMIN],
        allowed_user_ids=[member],
    )
    ingestor.persist(prepared)
    doc = _document(owner_engine, document_id)
    for chunk in _chunks(owner_engine, document_id):
        assert (chunk.classification, chunk.allowed_roles, chunk.acl_version) == (
            "restricted",
            ["admin"],
            doc.acl_version,
        )


def test_documents_deleted_before_processing_are_skipped(
    owner_engine: Engine, app_engine: Engine, seeded: SeededTenant, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path)
    admin = _admin(seeded)
    document_id = _upload(app_engine, storage, admin, "a.txt", b"short lived")
    documents_service.delete_document(app_engine, storage, admin, document_id)
    assert Ingestor(app_engine, storage, FakeEmbedder(), max_pdf_pages=50).run_once() is True
    assert _chunks(owner_engine, document_id) == []


def test_finished_ingestion_invalidates_cached_retrieval(
    owner_engine: Engine, app_engine: Engine, seeded: SeededTenant, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path)
    admin = _admin(seeded)
    embedder = FakeEmbedder()
    _upload(app_engine, storage, admin, "faq.txt", b"parking permits are issued by facilities")
    ttl = timedelta(minutes=10)
    assert retrieve(app_engine, embedder, admin, "parking permits", top_k=5, cache_ttl=ttl) == []
    Ingestor(app_engine, storage, embedder, max_pdf_pages=50).run_once()
    results = retrieve(app_engine, embedder, admin, "parking permits", top_k=5, cache_ttl=ttl)
    assert results
    assert "parking permits" in results[0].content


def test_pathological_pdf_fails_permanently_instead_of_stalling_the_worker(
    owner_engine: Engine, app_engine: Engine, seeded: SeededTenant, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path)
    document_id = _upload(
        app_engine, storage, _admin(seeded), "bomb.pdf", make_heavy_pdf(2_000_000)
    )
    ingestor = Ingestor(app_engine, storage, FakeEmbedder(), max_pdf_pages=50, timeout_seconds=2)
    assert ingestor.run_once() is True
    doc = _document(owner_engine, document_id)
    assert (doc.status, doc.error) == ("failed", "document took too long to process")
