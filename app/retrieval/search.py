"""Choosing the search that retrieves a question's chunks.

The service and the evaluation harness both search through here, so the strategy they
name is the strategy that runs.
"""

import uuid
from typing import Literal

from sqlalchemy.orm import Session

from app.retrieval.dense import TOP_K, RetrievedChunk, dense_search
from app.retrieval.hybrid import hybrid_search

Retriever = Literal["dense", "hybrid"]


def search(
    session: Session,
    retriever: Retriever,
    collection_id: uuid.UUID,
    question: str,
    query: list[float],
    limit: int = TOP_K,
) -> list[RetrievedChunk]:
    """Retrieve up to `limit` of the collection's chunks with the named search, best first.

    `query` is the question's embedding. Hybrid search also matches the question's text;
    dense search reads only the embedding.
    """
    if retriever == "hybrid":
        return hybrid_search(session, collection_id, question, query, limit)
    return dense_search(session, collection_id, query, limit)
