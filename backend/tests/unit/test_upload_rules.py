import hashlib

import pytest

from app.documents.validation import (
    UploadRejected,
    UploadTooLarge,
    sanitize_filename,
    validate_upload,
)
from tests.fakes import make_pdf

MAX = 1024


def test_accepts_text_markdown_and_pdf() -> None:
    assert validate_upload("notes.TXT", b"hello", max_bytes=MAX).kind == "txt"
    assert validate_upload("readme.md", b"# hi", max_bytes=MAX).mime_type == "text/markdown"
    pdf = make_pdf(["x"])
    result = validate_upload("report.pdf", pdf, max_bytes=10_000)
    assert (result.kind, result.size_bytes, result.sha256) == (
        "pdf",
        len(pdf),
        hashlib.sha256(pdf).hexdigest(),
    )


@pytest.mark.parametrize(
    ("filename", "data"),
    [
        ("evil.exe", b"MZ"),
        ("page.html", b"<script>"),
        ("noextension", b"hello"),
        ("fake.pdf", b"just text pretending"),
        ("bad.txt", b"\xff\xfe\xfa"),
        ("nul.md", b"a\x00b"),
        ("empty.txt", b""),
    ],
)
def test_rejects_disallowed_or_mislabelled_files(filename: str, data: bytes) -> None:
    with pytest.raises(UploadRejected):
        validate_upload(filename, data, max_bytes=MAX)


def test_rejects_oversized_files() -> None:
    with pytest.raises(UploadTooLarge):
        validate_upload("big.txt", b"x" * (MAX + 1), max_bytes=MAX)


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("../../etc/passwd.txt", "passwd.txt"),
        ("..\\..\\windows\\win.ini.txt", "win.ini.txt"),
        ("report\x00.pdf", "report_.pdf"),
        ("  .hidden.md ", "hidden.md"),
        ("", "upload"),
    ],
)
def test_filenames_are_reduced_to_a_safe_basename(raw: str, clean: str) -> None:
    assert sanitize_filename(raw) == clean
