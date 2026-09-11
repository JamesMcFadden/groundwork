import re

import pytest

from app.ingest.chunk import TextChunk, chunk_pages
from app.ingest.parse import Page


class WhitespaceTokenizer:
    """One token per run of non-space characters: deterministic and countable by eye.

    The real tokenizer is the embedding model's; windowing and page mapping do not
    depend on which tokenizer supplies the spans.
    """

    def spans(self, text: str) -> list[tuple[int, int]]:
        return [match.span() for match in re.finditer(r"\S+", text)]


class PieceTokenizer:
    """Splits each word into two-character pieces, the way WordPiece splits long words.

    Pieces of one word sit side by side with no gap between them, which is how the
    chunker tells a word's continuation from the start of the next word.
    """

    def spans(self, text: str) -> list[tuple[int, int]]:
        return [
            (start, min(start + 2, match.end()))
            for match in re.finditer(r"\S+", text)
            for start in range(match.start(), match.end(), 2)
        ]


def words(count: int) -> str:
    return " ".join(f"w{i}" for i in range(count))


def chunk(pages: list[Page], size: int = 4, overlap: int = 1) -> list[TextChunk]:
    return chunk_pages(pages, WhitespaceTokenizer(), size=size, overlap=overlap)


def test_a_short_document_is_a_single_chunk() -> None:
    chunks = chunk([Page(number=1, text="alpha beta gamma")])

    assert chunks == [
        TextChunk(index=0, text="alpha beta gamma", page_start=1, page_end=1, token_count=3)
    ]


def test_consecutive_chunks_share_exactly_the_overlap() -> None:
    chunks = chunk([Page(number=1, text=words(10))], size=4, overlap=1)

    assert [c.text for c in chunks] == ["w0 w1 w2 w3", "w3 w4 w5 w6", "w6 w7 w8 w9"]
    assert [c.token_count for c in chunks] == [4, 4, 4]


def test_no_trailing_chunk_is_made_entirely_of_overlap() -> None:
    """Stepping through every stride would add a fourth chunk, "w9", already covered."""
    chunks = chunk([Page(number=1, text=words(10))], size=4, overlap=1)

    assert len(chunks) == 3
    assert chunks[-1].text.endswith("w9")


def test_a_short_tail_with_new_tokens_is_kept() -> None:
    chunks = chunk([Page(number=1, text=words(5))], size=4, overlap=1)

    assert [c.text for c in chunks] == ["w0 w1 w2 w3", "w3 w4"]


def test_a_chunk_crossing_a_page_break_records_both_pages() -> None:
    chunks = chunk([Page(number=1, text="a b c"), Page(number=2, text="d e f")])

    assert (chunks[0].text, chunks[0].page_start, chunks[0].page_end) == ("a b c\nd", 1, 2)
    assert (chunks[1].page_start, chunks[1].page_end) == (2, 2)


def test_pages_after_an_empty_page_keep_their_numbers() -> None:
    """Empty pages are skipped, but numbering comes from the page, not its position."""
    pages = [Page(number=1, text="a b"), Page(number=2, text=""), Page(number=3, text="c d")]

    chunks = chunk(pages, size=2, overlap=0)

    assert [(c.text, c.page_start, c.page_end) for c in chunks] == [
        ("a b", 1, 1),
        ("c d", 3, 3),
    ]


def test_text_is_sliced_from_the_source_not_rebuilt_from_tokens() -> None:
    """Rejoining tokens would lose the source's spacing; a real decode loses far more."""
    source = "Keep  the   spacing, and Punctuation!"

    chunks = chunk([Page(number=1, text=source)], size=10)

    assert chunks[0].text == source


def test_indices_are_sequential_from_zero() -> None:
    chunks = chunk([Page(number=1, text=words(10))], size=4, overlap=1)

    assert [c.index for c in chunks] == [0, 1, 2]


def test_pages_without_text_produce_no_chunks() -> None:
    assert chunk([Page(number=1, text=""), Page(number=2, text="")]) == []


@pytest.mark.parametrize(("size", "overlap"), [(4, 4), (4, 5), (4, -1)])
def test_overlap_must_be_non_negative_and_smaller_than_size(size: int, overlap: int) -> None:
    """An overlap of at least the size would never advance the window."""
    with pytest.raises(ValueError, match="overlap"):
        chunk([Page(number=1, text=words(10))], size=size, overlap=overlap)


def test_a_window_never_ends_partway_through_a_word() -> None:
    """A fragment re-tokenizes to a different count than the window measured."""
    pages = [Page(number=1, text="aaaa bbbb cccc")]

    chunks = chunk_pages(pages, PieceTokenizer(), size=3, overlap=0)

    assert [(c.text, c.token_count) for c in chunks] == [
        ("aaaa", 2),
        ("bbbb", 2),
        ("cccc", 2),
    ]


def test_a_window_never_starts_partway_through_a_word() -> None:
    """The overlap grows back to a word boundary rather than starting on a fragment."""
    pages = [Page(number=1, text="aaaa bbbb cccc")]

    chunks = chunk_pages(pages, PieceTokenizer(), size=4, overlap=1)

    assert [c.text for c in chunks] == ["aaaa bbbb", "bbbb cccc"]


def test_a_word_longer_than_the_window_is_cut_rather_than_looped_on() -> None:
    pages = [Page(number=1, text="a" * 10)]

    chunks = chunk_pages(pages, PieceTokenizer(), size=2, overlap=1)

    assert "".join(c.text for c in chunks) == "a" * 10
