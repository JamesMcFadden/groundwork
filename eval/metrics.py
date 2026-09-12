"""Scoring retrieval against the golden set, as success-criteria.md defines it.

A retrieved chunk is a hit when it comes from an evidence location's document, its pages
include that location's page, and its text contains that location's quote. Page overlap
alone would credit neighbouring chunks that do not hold the answer; the quote is what
shows the chunk does.
"""

from collections.abc import Sequence

from app.retrieval.dense import RetrievedChunk
from eval.golden import Evidence, normalise


def is_hit(chunk: RetrievedChunk, evidence: Sequence[Evidence]) -> bool:
    """Whether the chunk holds any one of a question's evidence locations."""
    text = normalise(chunk.text)
    return any(
        chunk.filename == location.document
        and chunk.page_start <= location.page <= chunk.page_end
        and normalise(location.quote) in text
        for location in evidence
    )


def first_hit_rank(chunks: Sequence[RetrievedChunk], evidence: Sequence[Evidence]) -> int | None:
    """The rank, counted from 1 in retrieval order, of the first chunk that is a hit."""
    return next(
        (rank for rank, chunk in enumerate(chunks, start=1) if is_hit(chunk, evidence)), None
    )


def recall_at(ranks: Sequence[int | None], k: int) -> float:
    """The share of questions whose first hit ranks within k."""
    return _mean([1.0 if rank is not None and rank <= k else 0.0 for rank in ranks])


def mean_reciprocal_rank(ranks: Sequence[int | None], k: int) -> float:
    """The mean of 1/rank of each question's first hit within k, counting 0 where none is."""
    return _mean([1 / rank if rank is not None and rank <= k else 0.0 for rank in ranks])


def _mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("no questions to score")
    return sum(values) / len(values)
