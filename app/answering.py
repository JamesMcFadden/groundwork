"""Answering one question: embed it, search, generate, check citations, and record it all.

Every question is recorded, whatever happens to it, with the time each stage that ran
took. The search's transaction ends before the model is called, so a slow generation
never holds a pooled database connection.
"""

import logging
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import Question, RetrievalResult
from app.generation.citations import CheckedAnswer, check_citations
from app.generation.context import number_passages
from app.generation.generator import GenerationDeclined, GenerationError, Generator
from app.retrieval.dense import RetrievedChunk, dense_search
from app.services.embeddings import Embedder

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Citation:
    """One `[n]` marker in an answer, and the retrieved chunk it points at."""

    marker: int
    chunk: RetrievedChunk


@dataclass(frozen=True, slots=True)
class RecordedQuestion:
    """A question as recorded, with the citations its answer carries."""

    question: Question
    citations: list[Citation]


class Stopwatch:
    """Milliseconds per stage, recorded even when a stage raises."""

    def __init__(self) -> None:
        self._started = time.perf_counter()
        self.stages: dict[str, int] = {}

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.stages[name] = _ms_since(started)

    def total(self) -> int:
        return _ms_since(self._started)


def answer_question(
    session: Session,
    embedder: Embedder,
    generator: Generator,
    *,
    collection_id: uuid.UUID,
    user_id: uuid.UUID,
    text: str,
) -> RecordedQuestion:
    """Answer a question from one collection, and record the attempt whatever its outcome.

    The caller must already have checked that the collection belongs to the user.
    Declined and failed generations are recorded rather than raised; how to report them
    is the caller's decision.
    """
    watch = Stopwatch()
    with watch.stage("embed"):
        query = embedder.embed_query(text)
    with watch.stage("search"):
        chunks = dense_search(session, collection_id, query)
    # Ends the search's transaction, returning its connection to the pool, before a
    # model call that can take seconds.
    session.commit()

    question = Question(
        collection_id=collection_id, user_id=user_id, question_text=text, retriever="dense"
    )
    checked: CheckedAnswer | None = None
    if chunks:
        checked = _generate(question, text, chunks, generator, watch)
    else:
        # Nothing to answer from, so the model is not asked.
        question.outcome = "insufficient_evidence"

    # Measured before recording: a row cannot hold the time taken to write itself.
    _set_timings(question, watch)
    cited = checked.cited if checked is not None and question.outcome == "answered" else ()
    question.retrieval_results = [
        RetrievalResult(chunk_id=chunk.chunk_id, rank=rank, score=chunk.score, cited=rank in cited)
        for rank, chunk in enumerate(chunks, start=1)
    ]
    session.add(question)
    session.commit()
    return RecordedQuestion(
        question=question,
        citations=[Citation(marker=number, chunk=chunks[number - 1]) for number in cited],
    )


def _generate(
    question: Question,
    text: str,
    chunks: list[RetrievedChunk],
    generator: Generator,
    watch: Stopwatch,
) -> CheckedAnswer | None:
    """Ask the generator, check its citations, and set the outcome from both."""
    with watch.stage("prep"):
        passages = number_passages(chunks)
    try:
        with watch.stage("llm"):
            generation = generator.generate(text, passages)
    except GenerationDeclined as exc:
        logger.warning("generation declined in collection %s", question.collection_id)
        _set_failure(question, "declined", exc)
        return None
    except Exception as exc:
        logger.exception("generation failed in collection %s", question.collection_id)
        _set_failure(question, "failed", exc)
        return None

    checked = check_citations(generation, passages)
    question.input_tokens = generation.input_tokens
    question.output_tokens = generation.output_tokens
    question.invalid_citations = checked.invalid_citations
    if generation.insufficient_evidence or not checked.statements:
        # The model's own judgement, or an answer with no citation left standing.
        question.outcome = "insufficient_evidence"
    else:
        question.outcome = "answered"
        question.answer_text = checked.text
    return checked


def _set_failure(question: Question, outcome: str, exc: Exception) -> None:
    question.outcome = outcome
    # The class name only: a message can carry a provider's or this service's detail.
    question.error_class = type(exc).__name__
    if isinstance(exc, GenerationError):
        question.input_tokens = exc.input_tokens
        question.output_tokens = exc.output_tokens


def _set_timings(question: Question, watch: Stopwatch) -> None:
    question.embed_ms = watch.stages.get("embed")
    question.search_ms = watch.stages.get("search")
    question.prep_ms = watch.stages.get("prep")
    question.llm_ms = watch.stages.get("llm")
    question.total_ms = watch.total()


def _ms_since(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
