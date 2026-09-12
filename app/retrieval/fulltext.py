"""Full-text retrieval: the chunks of one collection sharing the most words with a question.

The question is stemmed, and stripped of stopwords, by the text search configuration the
chunks were indexed with. A chunk matches if it holds any of the words that remain.
`plainto_tsquery` alone requires all of them, and a question rarely shares every word with
the passage that answers it, so most questions would find nothing.
"""

import uuid
from typing import Any

from sqlalchemy import ColumnElement, Float, Text, cast, func, select
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.orm import Session

from app.db.models import TEXT_SEARCH_CONFIG, Chunk, Document
from app.retrieval.dense import TOP_K, RetrievedChunk


def fulltext_search(
    session: Session, collection_id: uuid.UUID, question: str, limit: int = TOP_K
) -> list[RetrievedChunk]:
    """Return up to `limit` of the collection's chunks holding any of the question's words.

    Chunks holding more of its words, or holding them more often, come first. Scores are
    `ts_rank` values, higher meaning a better match, on no scale shared with dense search.
    A question of stopwords alone has no words to match, and finds nothing.
    """
    query = _any_word(question)
    rank = func.ts_rank(Chunk.tsv, query, type_=Float).label("rank")
    best = (
        select(
            Chunk.id,
            Chunk.document_id,
            Chunk.text,
            Chunk.page_start,
            Chunk.page_end,
            rank,
        )
        # Matching `tsv` itself, not an expression over `text`, is what lets the GIN index
        # find the candidates.
        .where(Chunk.collection_id == collection_id, Chunk.tsv.bool_op("@@")(query))
        .order_by(rank.desc(), Chunk.id)
        .limit(limit)
        .subquery()
    )
    rows = session.execute(
        select(best, Document.filename)
        .join(Document, Document.id == best.c.document_id)
        # Chunk id breaks ties, so equal ranks come back in the same order every time.
        .order_by(best.c.rank.desc(), best.c.id)
    ).all()
    return [
        RetrievedChunk(
            chunk_id=row.id,
            document_id=row.document_id,
            filename=row.filename,
            text=row.text,
            page_start=row.page_start,
            page_end=row.page_end,
            score=row.rank,
        )
        for row in rows
    ]


def _any_word(question: str) -> ColumnElement[Any]:
    """A text search query matching chunks that hold any of the question's words.

    `plainto_tsquery` keeps a question's words as quoted lexemes, which hold no spaces, and
    joins them with ` & `. Replacing each join with ` | ` asks for any of them instead.
    """
    all_words = func.plainto_tsquery(TEXT_SEARCH_CONFIG, question)
    return cast(func.replace(cast(all_words, Text), " & ", " | "), TSQUERY)
