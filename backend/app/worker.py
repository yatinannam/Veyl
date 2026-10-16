"""Ingestion worker: python -m app.worker"""

import logging
import signal
import threading
from types import FrameType

from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.db.engine import create_db_engine
from app.documents.storage import LocalStorage
from app.ingestion.pipeline import Ingestor
from app.retrieval.embeddings import FastEmbedEmbedder

logger = logging.getLogger("veyl.worker")


def main() -> int:
    settings = Settings()
    logging.basicConfig(level=settings.log_level.upper())
    engine = create_db_engine(settings.database_url.get_secret_value())
    embedder = FastEmbedEmbedder(settings.embedding_model, settings.embedding_cache_dir)
    ingestor = Ingestor(
        engine,
        LocalStorage(settings.upload_dir),
        embedder,
        max_pdf_pages=settings.max_pdf_pages,
        max_chars=settings.max_extracted_chars,
        timeout_seconds=settings.extraction_timeout_seconds,
        memory_bytes=settings.extraction_memory_mb * 1024 * 1024,
    )
    stop = threading.Event()

    def _stop(signum: int, frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    logger.info("worker started (model %s)", embedder.model_name)
    while not stop.is_set():
        try:
            worked = ingestor.run_once()
        except OperationalError:
            logger.warning("database unavailable; retrying")
            worked = False
        if not worked:
            stop.wait(settings.worker_poll_seconds)
    engine.dispose()
    logger.info("worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
