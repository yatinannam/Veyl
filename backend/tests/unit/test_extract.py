import time

import pytest

from app.ingestion.extract import ExtractionError, extract_text, extract_text_isolated
from app.text import clean_text
from tests.fakes import encrypt_pdf, make_heavy_pdf, make_pdf


def test_text_and_markdown_are_decoded_as_utf8() -> None:
    assert extract_text("txt", "café menu".encode(), max_pdf_pages=10) == "café menu"
    assert extract_text("md", b"# Title\n\nBody", max_pdf_pages=10) == "# Title\n\nBody"


@pytest.mark.parametrize(
    ("data", "message"),
    [(b"\xff\xfe\x00bad", "UTF-8"), (b"ok\x00ok", "NUL")],
)
def test_bad_text_is_rejected(data: bytes, message: str) -> None:
    with pytest.raises(ExtractionError, match=message):
        extract_text("txt", data, max_pdf_pages=10)


def test_pdf_text_is_extracted_from_every_page() -> None:
    text = extract_text(
        "pdf", make_pdf(["Quarterly revenue grew", "Second page"]), max_pdf_pages=10
    )
    assert "Quarterly revenue grew" in text
    assert "Second page" in text


def test_encrypted_pdfs_are_rejected() -> None:
    with pytest.raises(ExtractionError, match="encrypted"):
        extract_text("pdf", encrypt_pdf(make_pdf(["secret"])), max_pdf_pages=10)


def test_pdfs_over_the_page_cap_are_rejected() -> None:
    with pytest.raises(ExtractionError, match="more than 2 pages"):
        extract_text("pdf", make_pdf(["a", "b", "c"]), max_pdf_pages=2)


def test_garbage_pdfs_are_rejected() -> None:
    with pytest.raises(ExtractionError, match="readable PDF"):
        extract_text("pdf", b"%PDF-1.4 this is not really a pdf", max_pdf_pages=10)


def test_unknown_kinds_are_rejected() -> None:
    with pytest.raises(ExtractionError):
        extract_text("exe", b"MZ", max_pdf_pages=10)


def _isolated(
    kind: str, data: bytes, *, max_chars: int = 1_000_000, timeout_seconds: float = 30
) -> str:
    return extract_text_isolated(
        kind, data, max_pdf_pages=10, max_chars=max_chars, timeout_seconds=timeout_seconds
    )


def test_isolated_extraction_returns_text() -> None:
    assert _isolated("md", b"# Hi\n\nthere") == "# Hi\n\nthere"


def test_isolated_extraction_reports_extraction_errors() -> None:
    with pytest.raises(ExtractionError, match="encrypted"):
        _isolated("pdf", encrypt_pdf(make_pdf(["secret"])))


def test_pathological_pdf_is_stopped_by_the_time_limit() -> None:
    # About 6 KB on disk, about 8 s of pypdf time unbounded (quadratic in stream size).
    started = time.monotonic()
    with pytest.raises(ExtractionError, match="too long"):
        _isolated("pdf", make_heavy_pdf(2_000_000), timeout_seconds=2)
    assert time.monotonic() - started < 6


def test_documents_with_too_much_text_are_rejected() -> None:
    with pytest.raises(ExtractionError, match="too much text"):
        _isolated("txt", b"word " * 1000, max_chars=100)


def test_clean_text_removes_nul_and_replaces_lone_surrogates() -> None:
    assert clean_text("a\x00b\ud800c") == "ab�c"


def test_pdf_text_with_nul_bytes_is_cleaned_not_rejected() -> None:
    text = extract_text("pdf", make_pdf(["Hello\x00World"]), max_pdf_pages=5)
    assert "\x00" not in text
    assert "Hello" in text
