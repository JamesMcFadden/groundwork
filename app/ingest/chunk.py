"""Splitting a document's pages into overlapping, token-bounded passages.

Windows run across page breaks. A page break is layout, not meaning: a paragraph that
continues onto the next page should be retrievable as one passage rather than two
fragments. Each chunk records the pages its first and last tokens came from.

Window edges fall only between words. The embedding model re-tokenizes a chunk's text
rather than reusing the chunker's tokens, and a fragment cut from partway through a
word tokenizes differently — enough, on real prose, to push a full chunk past the
model's window. Cut between words, the text reproduces its token count exactly.
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
    """Cut pages into windows of at most `size` tokens, overlapping by at least `overlap`.

    Windows shrink to end between words, and overlap grows to begin at one. The one
    exception is a single word longer than the window, which is cut rather than looped
    on. Pages without text contribute nothing, but page numbers come from `Page.number`
    rather than position, so a skipped page never shifts the numbering after it.
    """
    if not 0 <= overlap < size:
        raise ValueError(f"need 0 <= overlap < size, got overlap={overlap} size={size}")

    text, page_offsets, page_numbers = _join(pages)
    spans = tokenizer.spans(text)
    word_starts = _word_starts(spans)

    chunks: list[TextChunk] = []
    start = 0
    while start < len(spans):
        end = _end_between_words(start, min(start + size, len(spans)), word_starts)
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
        # Stopping once a window reaches the end, rather than stepping on regardless, is
        # what prevents a trailing chunk made entirely of overlap.
        if end == len(spans):
            break
        start = _next_start(start, end, overlap, word_starts)

    return chunks


def _word_starts(spans: list[tuple[int, int]]) -> list[bool]:
    """Mark the tokens that begin a word: the first, and any with a gap before it.

    Tokens with nothing between them are pieces of one word ("pdf", "##s") or a word and
    its punctuation, and a window edge between them would leave a fragment.
    """
    return [i == 0 or spans[i][0] > spans[i - 1][1] for i in range(len(spans))]


def _end_between_words(start: int, end: int, word_starts: list[bool]) -> int:
    """Pull a window's end back to a word boundary, unless one word fills the window."""
    if end == len(word_starts):
        return end
    boundary = end
    while boundary > start and not word_starts[boundary]:
        boundary -= 1
    return boundary if boundary > start else end


def _next_start(start: int, end: int, overlap: int, word_starts: list[bool]) -> int:
    """Begin the next window `overlap` tokens back, moved earlier to a word boundary.

    Moving earlier rather than later grows the overlap instead of shrinking it, so a
    sentence crossing the boundary still appears whole in one chunk. With no boundary
    between the two starts, the next window begins where this one ended.
    """
    candidate = end - overlap
    while candidate > start and not word_starts[candidate]:
        candidate -= 1
    return candidate if candidate > start else end


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
