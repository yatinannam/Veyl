import io

from pypdf import PdfReader

_TEXT_KINDS = frozenset({"txt", "md"})


class ExtractionError(Exception):
    """Ingestion failed for a reason that is safe to show the uploader."""


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
