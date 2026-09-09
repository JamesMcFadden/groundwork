import pymupdf
import pytest

from app.ingest.parse import ParseError, parse_pdf


def make_pdf(pages: list[str]) -> bytes:
    """Build a PDF holding this text, one entry per page.

    Generating fixtures beats committing binaries: each test states the document it
    depends on, and there is nothing opaque in the repository to keep in sync.
    """
    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    # pymupdf types tobytes() as Any; the call narrows it for warn_return_any.
    return bytes(document.tobytes())


def test_returns_one_page_per_pdf_page() -> None:
    pages = parse_pdf(make_pdf(["alpha", "beta", "gamma"]))

    assert [page.text for page in pages] == ["alpha", "beta", "gamma"]


def test_pages_are_numbered_from_one() -> None:
    """1-based, because the number reaches a reader through a citation."""
    pages = parse_pdf(make_pdf(["alpha", "beta"]))

    assert [page.number for page in pages] == [1, 2]


def test_a_page_without_text_keeps_its_position() -> None:
    """Dropping it would renumber every page after it and misdirect their citations."""
    pages = parse_pdf(make_pdf(["alpha", "", "gamma"]))

    assert [(page.number, page.text) for page in pages] == [
        (1, "alpha"),
        (2, ""),
        (3, "gamma"),
    ]


def test_extraction_whitespace_is_collapsed() -> None:
    pages = parse_pdf(make_pdf(["alpha\nbeta"]))

    assert pages[0].text == "alpha beta"


def test_a_pdf_with_no_text_anywhere_is_rejected() -> None:
    """The scanned-document case: zero chunks would look like successful ingestion."""
    with pytest.raises(ParseError, match="no extractable text"):
        parse_pdf(make_pdf(["", ""]))


def test_bytes_that_are_not_a_pdf_are_rejected() -> None:
    with pytest.raises(ParseError, match="could not read pdf"):
        parse_pdf(b"this is not a pdf")


def test_empty_bytes_are_rejected() -> None:
    with pytest.raises(ParseError, match="could not read pdf"):
        parse_pdf(b"")
