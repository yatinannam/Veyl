import pytest

from app.ingestion.extract import ExtractionError, extract_text
from tests.fakes import encrypt_pdf, make_pdf


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
