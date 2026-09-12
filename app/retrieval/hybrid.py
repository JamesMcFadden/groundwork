"""Hybrid retrieval: dense and full-text search over one collection, fused by rank.

Dense search matches meaning but blurs exact terms; full-text search matches exact words
but knows no synonyms. Reciprocal rank fusion combines their results by position alone:
inner products and `ts_rank` values have unrelated scales, so their scores are never added.
"""

import uuid
from collections.abc import Sequence
from dataclasses import replace

from sqlalchemy.orm import Session

from app.retrieval.dense import TOP_K, RetrievedChunk, dense_search
from app.retrieval.fulltext import fulltext_search

# From Cormack, Clarke, and Büttcher (2009), taken as given rather than tuned on the golden
# set. The larger it is, the less a first place in one list counts against a place in both.
RRF_K = 60

# Candidates taken from each search, whatever the result limit, so a search's top five are
# always the first five of its top ten.
CANDIDATES = 50


def hybrid_search(
    session: Session,
    collection_id: uuid.UUID,
    question: str,
    query: list[float],
    limit: int = TOP_K,
) -> list[RetrievedChunk]:
    """Return up to `limit` of the collection's chunks, best fused score first.

    `query` is the question's embedding, for dense search; `question` is its text, for
    full-text search. A question with no words to match by full text is ranked by dense
    search alone.
    """
    # One transaction for both, where dense search's index setting applies.
    rankings = [
        dense_search(session, collection_id, query, limit=CANDIDATES),
        fulltext_search(session, collection_id, question, limit=CANDIDATES),
    ]
    return reciprocal_rank_fusion(rankings, limit)


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[RetrievedChunk]], limit: int
) -> list[RetrievedChunk]:
    """Fuse rankings into one, scoring each chunk the sum of 1 / (RRF_K + rank) over them.

    Ranks count from 1. Equal scores are ordered by chunk id, so the same rankings always
    fuse the same way. Each result is its chunk as first found, with the fused score in
    place of the score its search gave it.
    """
    scores: dict[int, float] = {}
    found: dict[int, RetrievedChunk] = {}
    for ranking in rankings:
        for rank, chunk in enumerate(ranking, start=1):
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1 / (RRF_K + rank)
            found.setdefault(chunk.chunk_id, chunk)
    best = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))[:limit]
    return [replace(found[chunk_id], score=scores[chunk_id]) for chunk_id in best]
