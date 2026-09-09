"""Extracting text from PDFs, one page at a time.

Page-level extraction is what makes citations possible: a chunk records the pages it
came from, and a reader is sent to a page rather than to a document.
"""

from dataclasses import dataclass

import pymupdf


class ParseError(Exception):
    """A PDF that cannot be turned into text.

    Covers a file that will not open — corrupt, encrypted, not a PDF at all — and one
    that opens but holds no extractable text, which is the scanned-document case
    success-criteria.md puts out of scope. Neither is worth retrying, so both are
    terminal for the job that hits them.
    """


@dataclass(frozen=True, slots=True)
class Page:
    """One page's text, numbered the way a reader numbers it.

    The number is 1-based and carried explicitly rather than left as a list index,
    because it survives into `chunks.page_start` and from there into what a citation
    shows. An off-by-one here stays invisible until someone follows a citation to the
    wrong page.
    """

    number: int
    text: str


def parse_pdf(data: bytes) -> list[Page]:
    """Extract each page's text, in order.

    Pages with no text are kept as empty strings rather than dropped: position is what
    gives a page its number, so removing one would renumber every page after it. The
    chunker skips them instead.

    Raises `ParseError` if the file will not open, or holds no text anywhere.
    """
    try:
        with pymupdf.open(stream=data, filetype="pdf") as document:
            # pages() rather than the document itself: Document defines __getitem__ but
            # no __iter__, so iterating it directly leans on the legacy sequence
            # protocol, which stricter type checkers reject.
            pages = [
                Page(number=index + 1, text=_normalise(page.get_text()))
                for index, page in enumerate(document.pages())
            ]
    except Exception as exc:
        raise ParseError(f"could not read pdf: {exc}") from exc

    if not any(page.text for page in pages):
        raise ParseError("no extractable text; scanned and image-only PDFs are out of scope")

    return pages


def _normalise(text: str) -> str:
    """Collapse extraction whitespace, leaving the words themselves alone.

    Runs of whitespace become single spaces, which steadies token counts and stops
    chunk boundaries landing on layout artefacts. Line-break hyphenation is
    deliberately not repaired: nothing distinguishes a soft hyphen from a real one, and
    joining "well-known" into "wellknown" would corrupt both retrieval and the text a
    citation quotes.
    """
    return " ".join(text.split())
