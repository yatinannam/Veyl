"""Ingestion worker: python -m app.worker, or a background thread of the API process when
RUN_INGESTION_WORKER is set."""

import logging
import signal
import threading
from types import FrameType

from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.db.engine import create_db_engine
from app.documents.storage import LocalStorage
from app.ingestion.pipeline import Ingestor
from app.retrieval.embeddings import Embedder, FastEmbedEmbedder

logger = logging.getLogger("veyl.worker")


def build_ingestor(settings: Settings, engine: Engine, embedder: Embedder) -> Ingestor:
    return Ingestor(
        engine,
        LocalStorage(settings.upload_dir),
        embedder,
        max_pdf_pages=settings.max_pdf_pages,
        max_chars=settings.max_extracted_chars,
        timeout_seconds=settings.extraction_timeout_seconds,
        memory_bytes=settings.extraction_memory_mb * 1024 * 1024,
    )


def run_until_stopped(ingestor: Ingestor, stop: threading.Event, poll_seconds: float) -> None:
    while not stop.is_set():
        try:
            worked = ingestor.run_once()
        except OperationalError:
            logger.warning("database unavailable; retrying")
            worked = False
        except Exception:
            # Never let one bad poll end the loop: in the API process nothing would
            # restart it. A claimed job's lease expires and it is retried.
            logger.exception("ingestion poll failed; retrying")
            worked = False
        if not worked:
            stop.wait(poll_seconds)


class BackgroundWorker:
    """The ingestion loop on a daemon thread, stopped with the API's lifespan."""

    def __init__(self, ingestor: Ingestor, poll_seconds: float) -> None:
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=run_until_stopped,
            args=(ingestor, self._stop, poll_seconds),
            name="veyl-ingestion",
            daemon=True,
        )

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def start(self) -> None:
        self._thread.start()
        logger.info("ingestion worker thread started")

    def stop(self, timeout_seconds: float) -> None:
        """Ask the loop to stop and wait for the current document, up to the timeout. An
        unfinished job is left to its lease and picked up again on the next start."""
        self._stop.set()
        self._thread.join(timeout_seconds)
        if self._thread.is_alive():
            logger.warning("ingestion worker still busy at shutdown; its job will be retried")
        else:
            logger.info("ingestion worker thread stopped")


def main() -> int:
    settings = Settings()
    logging.basicConfig(level=settings.log_level.upper())
    engine = create_db_engine(
        settings.database_url.get_secret_value(),
        connect_timeout_seconds=settings.database_connect_timeout_seconds,
    )
    embedder = FastEmbedEmbedder(
        settings.embedding_model, settings.embedding_cache_dir, settings.embedding_batch_size
    )
    ingestor = build_ingestor(settings, engine, embedder)
    stop = threading.Event()

    def _stop(signum: int, frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    logger.info("worker started (model %s)", embedder.model_name)
    run_until_stopped(ingestor, stop, settings.worker_poll_seconds)
    engine.dispose()
    logger.info("worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
