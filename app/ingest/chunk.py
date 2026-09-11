"""Splitting a document's pages into overlapping, token-bounded passages.

Windows run across page breaks. A page break is layout, not meaning: a paragraph that
continues onto the next page should be retrievable as one passage rather than two
fragments. Each chunk records the pages its first and last tokens came from.
"""

import bisect
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.ingest.parse import Page

# bge-small-en-v1.5 accepts 512 tokens including its two special tokens, and truncates
# anything longer before embedding — silently, so an oversized chunk would still be
# stored and quoted whole while its tail went unsearchable.
CHUNK_TOKENS = 510

# Enough that a sentence crossing a window boundary appears whole in at least one chunk.
OVERLAP_TOKENS = 64

# Joins consecutive pages so the last word of one and the first of the next cannot
# fuse into a single token.
PAGE_SEPARATOR = "\n"


class Tokenizer(Protocol):
    """What chunking needs from a tokenizer: where each token sits in the text.

    Character spans rather than token ids, because chunk text is sliced from the source.
    Decoding ids does not reproduce the text a citation should quote. The tokenizer must
    be the embedding model's own, or token counts stop meaning what the model counts.
    """

    def spans(self, text: str) -> list[tuple[int, int]]: ...


@dataclass(frozen=True, slots=True)
class TextChunk:
    """A passage ready to embed. Named apart from the `Chunk` model it will become."""

    index: int
    text: str
    page_start: int
    page_end: int
    token_count: int


def chunk_pages(
    pages: Sequence[Page],
    tokenizer: Tokenizer,
    size: int = CHUNK_TOKENS,
    overlap: int = OVERLAP_TOKENS,
) -> list[TextChunk]:
    """Cut pages into windows of `size` tokens, each sharing `overlap` with the last.

    Pages without text contribute nothing, but page numbers come from `Page.number`
    rather than position, so a skipped page never shifts the numbering after it.
    """
    if not 0 <= overlap < size:
        raise ValueError(f"need 0 <= overlap < size, got overlap={overlap} size={size}")

    text, page_offsets, page_numbers = _join(pages)
    spans = tokenizer.spans(text)
    stride = size - overlap

    chunks: list[TextChunk] = []
    start = 0
    while start < len(spans):
        end = min(start + size, len(spans))
        first_char, last_char = spans[start][0], spans[end - 1][1]
        chunks.append(
            TextChunk(
                index=len(chunks),
                text=text[first_char:last_char],
                page_start=_page_at(first_char, page_offsets, page_numbers),
                page_end=_page_at(last_char - 1, page_offsets, page_numbers),
                token_count=end - start,
            )
        )
        # Stopping once a window reaches the end, rather than stepping through every
        # stride, is what prevents a trailing chunk made entirely of overlap.
        if end == len(spans):
            break
        start += stride

    return chunks


def _join(pages: Sequence[Page]) -> tuple[str, list[int], list[int]]:
    """Join non-empty pages, recording where each begins and which page it is."""
    parts: list[str] = []
    offsets: list[int] = []
    numbers: list[int] = []
    position = 0
    for page in pages:
        if not page.text:
            continue
        if parts:
            position += len(PAGE_SEPARATOR)
        offsets.append(position)
        numbers.append(page.number)
        parts.append(page.text)
        position += len(page.text)
    return PAGE_SEPARATOR.join(parts), offsets, numbers


def _page_at(char: int, offsets: list[int], numbers: list[int]) -> int:
    """Return the number of the page containing this character of the joined text."""
    return numbers[bisect.bisect_right(offsets, char) - 1]
