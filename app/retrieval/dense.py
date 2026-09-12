"""Dense retrieval: the chunks of one collection nearest a question's embedding.

Similarity is the inner product. bge-small-en-v1.5 vectors are unit-length, so the inner
product equals cosine similarity and ranks chunks identically.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Chunk, Document

# The prompt numbers five passages, and Recall@5 is the retrieval target.
TOP_K = 5


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """A chunk found for a question, with what a citation needs to point back at it."""

    chunk_id: int
    document_id: uuid.UUID
    filename: str
    text: str
    page_start: int
    page_end: int
    score: float


def dense_search(
    session: Session, collection_id: uuid.UUID, query: list[float], limit: int = TOP_K
) -> list[RetrievedChunk]:
    """Return up to `limit` of the collection's chunks, most similar first.

    Scores are inner products: in [-1, 1] for unit vectors, higher meaning closer.

    The nearest chunks are chosen from `chunks` alone, filtering on its denormalised
    `collection_id`, and only those few are joined to their documents. The ordered scan
    stays on one table, where a vector index can serve it.
    """
    # pgvector's <#> is the negative inner product, so ascending order is nearest first.
    distance = Chunk.embedding.max_inner_product(query).label("distance")
    nearest = (
        select(
            Chunk.id,
            Chunk.document_id,
            Chunk.text,
            Chunk.page_start,
            Chunk.page_end,
            distance,
        )
        .where(Chunk.collection_id == collection_id)
        .order_by(distance)
        .limit(limit)
        .subquery()
    )
    rows = session.execute(
        select(nearest, Document.filename)
        .join(Document, Document.id == nearest.c.document_id)
        # Chunk id breaks ties, so equal scores come back in the same order every time.
        .order_by(nearest.c.distance, nearest.c.id)
    ).all()
    return [
        RetrievedChunk(
            chunk_id=row.id,
            document_id=row.document_id,
            filename=row.filename,
            text=row.text,
            page_start=row.page_start,
            page_end=row.page_end,
            score=-row.distance,
        )
        for row in rows
    ]
