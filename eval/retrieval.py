"""Running the golden set's questions through the service's own search.

Questions are searched with the retriever the run names, through the selection the service
answers with, so the figures describe the strategy that would serve them.

Recall@5 is measured on the same five-result search the service answers from, and
MRR@10 on a separate ten-result search. With an approximate index the ten nearest need
not begin with the five nearest, and the headline figure must describe what the service
actually retrieves.

Under dense search each question's best chunk score is recorded too, answerable and
unanswerable alike: it is the data a relevance-score threshold would be chosen from. None
is applied. Hybrid search's fused scores are built from ranks and say nothing about a
threshold, so none is recorded under it.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from app.retrieval.dense import TOP_K, RetrievedChunk
from app.retrieval.search import Retriever, search
from eval.golden import GoldenSet
from eval.metrics import first_hit_rank, mean_reciprocal_rank, recall_at

# The service answers from TOP_K passages, so Recall@5 is Recall@TOP_K.
RECALL_K = TOP_K
MRR_K = 10

# Turns a question into the vector search compares with passages. Passed in rather than
# fixed, so the same questions can be embedded more than one way and compared.
QueryEmbedder = Callable[[str], list[float]]


@dataclass(frozen=True, slots=True)
class AnswerableResult:
    """Where one answerable question's evidence ranked; None where it was not retrieved."""

    question_id: str
    rank_at_5: int | None
    rank_at_10: int | None
    best_score: float | None


@dataclass(frozen=True, slots=True)
class UnanswerableResult:
    question_id: str
    best_score: float | None


@dataclass(frozen=True, slots=True)
class RetrievalReport:
    retriever: Retriever
    answerable: tuple[AnswerableResult, ...]
    unanswerable: tuple[UnanswerableResult, ...]

    @property
    def hits_at_5(self) -> int:
        return sum(1 for result in self.answerable if result.rank_at_5 is not None)

    @property
    def recall_at_5(self) -> float:
        return recall_at([result.rank_at_5 for result in self.answerable], RECALL_K)

    @property
    def mrr_at_10(self) -> float:
        return mean_reciprocal_rank([result.rank_at_10 for result in self.answerable], MRR_K)


def run_retrieval(
    sessions: sessionmaker[Session],
    embed_query: QueryEmbedder,
    collection_id: uuid.UUID,
    golden: GoldenSet,
    retriever: Retriever,
) -> RetrievalReport:
    """Search the collection for every golden-set question, and score what comes back."""
    answerable: list[AnswerableResult] = []
    for question in golden.answerable:
        vector = embed_query(question.question)
        top_5 = _search(sessions, retriever, collection_id, question.question, vector, RECALL_K)
        top_10 = _search(sessions, retriever, collection_id, question.question, vector, MRR_K)
        answerable.append(
            AnswerableResult(
                question_id=question.id,
                rank_at_5=first_hit_rank(top_5, question.evidence),
                rank_at_10=first_hit_rank(top_10, question.evidence),
                best_score=_best_score(retriever, top_5),
            )
        )

    unanswerable: list[UnanswerableResult] = []
    for other in golden.unanswerable:
        vector = embed_query(other.question)
        top_5 = _search(sessions, retriever, collection_id, other.question, vector, RECALL_K)
        unanswerable.append(
            UnanswerableResult(question_id=other.id, best_score=_best_score(retriever, top_5))
        )
    return RetrievalReport(
        retriever=retriever, answerable=tuple(answerable), unanswerable=tuple(unanswerable)
    )


def _search(
    sessions: sessionmaker[Session],
    retriever: Retriever,
    collection_id: uuid.UUID,
    question: str,
    vector: list[float],
    limit: int,
) -> list[RetrievedChunk]:
    # One transaction per search, as the service runs it: search's index settings are
    # scoped to the transaction they are set in.
    with sessions() as session:
        return search(session, retriever, collection_id, question, vector, limit=limit)


def _best_score(retriever: Retriever, chunks: list[RetrievedChunk]) -> float | None:
    """The score of the nearest chunk the service would answer from, under dense search."""
    if retriever != "dense" or not chunks:
        return None
    return chunks[0].score
