import io
import multiprocessing
import sys
from contextlib import suppress
from multiprocessing.connection import Connection

from pypdf import PdfReader

_TEXT_KINDS = frozenset({"txt", "md"})


class ExtractionError(Exception):
    """Ingestion failed for a reason that is safe to show the uploader."""


def _extract_in_child(
    kind: str,
    data: bytes,
    max_pdf_pages: int,
    max_chars: int,
    memory_bytes: int | None,
    result: Connection,
) -> None:
    try:
        # Address-space limits exist on Linux (where the worker container runs), not Windows.
        if memory_bytes and sys.platform != "win32":
            import resource

            with suppress(ValueError, OSError):
                resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        text = extract_text(kind, data, max_pdf_pages=max_pdf_pages)
        if len(text) > max_chars:
            raise ExtractionError("document has too much text")
        result.send(("ok", text))
    except ExtractionError as exc:
        result.send(("error", str(exc)))
    except MemoryError:
        result.send(("error", "document is too large to process"))
    except Exception:
        result.send(("error", "file could not be processed"))
    finally:
        result.close()


def extract_text_isolated(
    kind: str,
    data: bytes,
    *,
    max_pdf_pages: int,
    max_chars: int,
    timeout_seconds: float,
    memory_bytes: int | None = None,
) -> str:
    """Run extraction in a child process with a wall-clock limit (and, on Linux, a memory
    limit), so a hostile file cannot stall or exhaust the worker. Parser time on crafted
    PDFs grows quadratically with content size; a few KB can cost minutes."""
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_extract_in_child,
        args=(kind, data, max_pdf_pages, max_chars, memory_bytes, sender),
        daemon=True,
    )
    process.start()
    sender.close()
    try:
        if not receiver.poll(timeout_seconds):
            raise ExtractionError("document took too long to process")
        status, value = receiver.recv()
    except EOFError as exc:  # the child died, e.g. killed for exceeding its memory limit
        raise ExtractionError("file could not be processed") from exc
    finally:
        if process.is_alive():
            process.kill()
        process.join(timeout=5)
        receiver.close()
    if status == "error":
        raise ExtractionError(value)
    return str(value)


def extract_text(kind: str, data: bytes, *, max_pdf_pages: int) -> str:
    if kind == "pdf":
        return _pdf_text(data, max_pdf_pages)
    if kind in _TEXT_KINDS:
        return _utf8_text(data)
    raise ExtractionError(f"unsupported document type: {kind}")


def _utf8_text(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExtractionError("file is not valid UTF-8 text") from exc
    if "\x00" in text:
        raise ExtractionError("file contains NUL bytes")
    return text


def _pdf_text(data: bytes, max_pages: int) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        encrypted = reader.is_encrypted
        page_count = 0 if encrypted else len(reader.pages)
    except Exception as exc:  # malformed input can raise many exception types
        raise ExtractionError("file is not a readable PDF") from exc
    if encrypted:
        raise ExtractionError("encrypted PDFs are not supported")
    if page_count > max_pages:
        raise ExtractionError(f"PDF has more than {max_pages} pages")
    try:
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise ExtractionError("file is not a readable PDF") from exc
