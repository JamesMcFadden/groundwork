"""Numbering retrieved chunks into the context a generator answers from.

Chunk ids never reach the model. Each request numbers its passages from 1 in retrieval
order, and the server maps a cited number back to its chunk by position. Small integers
cost fewer tokens and are cited more reliably than database ids, and because the mapping
exists only for this request, a citation can be checked exactly: a number either names
one of the passages supplied or it is invalid.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from html import escape

from app.retrieval.dense import RetrievedChunk


@dataclass(frozen=True, slots=True)
class Passage:
    """A retrieved chunk as a generator sees it: numbered, with no database identity."""

    number: int
    text: str
    filename: str
    page_start: int
    page_end: int

    @property
    def pages(self) -> str:
        if self.page_start == self.page_end:
            return str(self.page_start)
        return f"{self.page_start}-{self.page_end}"


def number_passages(chunks: Sequence[RetrievedChunk]) -> list[Passage]:
    """Number chunks from 1 in retrieval order, so passage n is `chunks[n - 1]`."""
    return [
        Passage(
            number=number,
            text=chunk.text,
            filename=chunk.filename,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
        )
        for number, chunk in enumerate(chunks, start=1)
    ]


def render_context(passages: Sequence[Passage]) -> str:
    """Lay passages out for a prompt, each tagged with its number and source.

    Filenames come from uploads, so they are escaped: a name containing a quote must not
    be able to close its attribute and pose as another passage's number or pages.
    """
    return "\n\n".join(
        f'<passage number="{passage.number}" source="{escape(passage.filename)}" '
        f'pages="{passage.pages}">\n{passage.text}\n</passage>'
        for passage in passages
    )
