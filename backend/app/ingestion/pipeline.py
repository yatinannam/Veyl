import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import Connection, Engine, delete, func, insert, select, text, update

from app.db.schema import document_chunks, documents, ingestion_jobs
from app.db.tenancy import tenant_transaction
from app.documents.storage import LocalStorage
from app.documents.validation import KIND_BY_MIME
from app.ingestion.chunker import chunk_text
from app.ingestion.extract import ExtractionError, extract_text
from app.retrieval.embeddings import Embedder

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
STALE_AFTER = timedelta(minutes=10)
RETRY_BACKOFF = timedelta(seconds=30)


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    job_id: UUID
    tenant_id: UUID
    document_id: UUID
    attempts: int


@dataclass(frozen=True, slots=True)
class PreparedDocument:
    job: ClaimedJob
    chunks: list[str]
    embeddings: list[list[float]]


def claim_job(engine: Engine) -> ClaimedJob | None:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT * FROM ingestion_claim_job(:stale)"), {"stale": STALE_AFTER}
        ).one_or_none()
    if row is None:
        return None
    return ClaimedJob(row.job_id, row.tenant_id, row.document_id, row.attempts)


def _finish_job(conn: Connection, job: ClaimedJob, status: str, error: str | None = None) -> None:
    conn.execute(
        update(ingestion_jobs)
        .where(ingestion_jobs.c.id == job.job_id)
        .values(status=status, last_error=error, locked_at=None, updated_at=func.now())
    )


class Ingestor:
    def __init__(
        self, engine: Engine, storage: LocalStorage, embedder: Embedder, *, max_pdf_pages: int
    ) -> None:
        self.engine = engine
        self.storage = storage
        self.embedder = embedder
        self.max_pdf_pages = max_pdf_pages

    def prepare(self, job: ClaimedJob) -> PreparedDocument | None:
        """Extract, chunk, and embed outside any long transaction."""
        with tenant_transaction(self.engine, job.tenant_id) as conn:
            doc = conn.execute(
                select(
                    documents.c.storage_key, documents.c.mime_type, documents.c.deleted_at
                ).where(documents.c.id == job.document_id)
            ).one_or_none()
            if doc is None or doc.deleted_at is not None:
                _finish_job(conn, job, "done")
                return None
            conn.execute(
                update(documents)
                .where(documents.c.id == job.document_id)
                .values(status="processing", updated_at=func.now())
            )
        data = self.storage.read(doc.storage_key)
        text_content = extract_text(
            KIND_BY_MIME[doc.mime_type], data, max_pdf_pages=self.max_pdf_pages
        )
        pieces = chunk_text(text_content)
        if not pieces:
            raise ExtractionError("no extractable text found")
        return PreparedDocument(job, pieces, self.embedder.embed_documents(pieces))

    def persist(self, prepared: PreparedDocument) -> None:
        """Copy the document's ACL onto the chunks while holding FOR SHARE on the document,
        so a concurrent permission change either finishes first (and we copy the new ACL) or
        waits and then rewrites these chunks itself."""
        job = prepared.job
        with tenant_transaction(self.engine, job.tenant_id) as conn:
            doc = conn.execute(
                select(
                    documents.c.classification,
                    documents.c.allowed_roles,
                    documents.c.allowed_user_ids,
                    documents.c.acl_version,
                    documents.c.deleted_at,
                )
                .where(documents.c.id == job.document_id)
                .with_for_update(read=True)
            ).one_or_none()
            if doc is None or doc.deleted_at is not None:
                _finish_job(conn, job, "done")
                return
            conn.execute(
                delete(document_chunks).where(document_chunks.c.document_id == job.document_id)
            )
            conn.execute(
                insert(document_chunks),
                [
                    {
                        "tenant_id": job.tenant_id,
                        "document_id": job.document_id,
                        "chunk_index": index,
                        "content": content,
                        "embedding": embedding,
                        "classification": doc.classification,
                        "allowed_roles": doc.allowed_roles,
                        "allowed_user_ids": doc.allowed_user_ids,
                        "acl_version": doc.acl_version,
                    }
                    for index, (content, embedding) in enumerate(
                        zip(prepared.chunks, prepared.embeddings, strict=True)
                    )
                ],
            )
            conn.execute(
                update(documents)
                .where(documents.c.id == job.document_id)
                .values(status="ready", error=None, updated_at=func.now())
            )
            _finish_job(conn, job, "done")

    def fail(self, job: ClaimedJob, error: Exception) -> None:
        permanent = isinstance(error, ExtractionError)
        message = str(error) if permanent else "ingestion failed"
        with tenant_transaction(self.engine, job.tenant_id) as conn:
            if permanent or job.attempts >= MAX_ATTEMPTS:
                _finish_job(conn, job, "failed", message)
                conn.execute(
                    update(documents)
                    .where(documents.c.id == job.document_id)
                    .values(status="failed", error=message, updated_at=func.now())
                )
            else:
                conn.execute(
                    update(ingestion_jobs)
                    .where(ingestion_jobs.c.id == job.job_id)
                    .values(
                        status="queued",
                        last_error=message,
                        locked_at=None,
                        run_after=func.now() + RETRY_BACKOFF * job.attempts,
                        updated_at=func.now(),
                    )
                )
                conn.execute(
                    update(documents)
                    .where(documents.c.id == job.document_id)
                    .values(status="pending", updated_at=func.now())
                )

    def run_once(self) -> bool:
        job = claim_job(self.engine)
        if job is None:
            return False
        try:
            prepared = self.prepare(job)
            if prepared is not None:
                self.persist(prepared)
        except Exception as exc:
            logger.warning(
                "ingestion attempt %d failed for document %s: %s",
                job.attempts,
                job.document_id,
                type(exc).__name__,
            )
            self.fail(job, exc)
        return True
