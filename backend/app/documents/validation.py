import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.errors import AppError

MIME_BY_KIND = {"pdf": "application/pdf", "txt": "text/plain", "md": "text/markdown"}
KIND_BY_MIME = {mime: kind for kind, mime in MIME_BY_KIND.items()}
_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]")


class UploadRejected(AppError):
    status_code = 422
    code = "upload_rejected"
    default_message = "The file was rejected."


class UploadTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"
    default_message = "Upload exceeds the size limit."


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    kind: str
    mime_type: str
    size_bytes: int
    sha256: str
    original_filename: str


def sanitize_filename(name: str) -> str:
    """Keep only a safe basename for display. It is never used as a path."""
    base = PurePosixPath(name.replace("\\", "/")).name
    cleaned = _UNSAFE.sub("_", base).strip(" .")
    return (cleaned or "upload")[:255]


def validate_upload(filename: str, data: bytes, *, max_bytes: int) -> ValidatedUpload:
    if len(data) > max_bytes:
        raise UploadTooLarge()
    if not data:
        raise UploadRejected("The file is empty.")
    safe_name = sanitize_filename(filename)
    kind = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
    if kind not in MIME_BY_KIND:
        raise UploadRejected("Only PDF, TXT, and Markdown files are accepted.")
    if kind == "pdf":
        if not data.startswith(b"%PDF-"):
            raise UploadRejected("The file is not a PDF.")
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UploadRejected("Text files must be UTF-8.") from exc
        if "\x00" in text:
            raise UploadRejected("Text files must not contain NUL bytes.")
    return ValidatedUpload(
        kind=kind,
        mime_type=MIME_BY_KIND[kind],
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        original_filename=safe_name,
    )
