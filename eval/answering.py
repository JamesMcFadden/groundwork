"""The golden set's questions through the service's answering path, scored from its records.

Each question goes through `answer_question`, exactly as `POST /questions` runs it, and
is scored from what the service wrote to `questions` and `retrieval_results` rather than
from what the call returned: the tables are the service's own account of what happened.
"""

import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.answering import answer_question
from app.db.models import Question, RetrievalResult
from app.generation.generator import Generator
from app.services.embeddings import Embedder
from eval.golden import GoldenSet
from eval.ingest import IngestedCorpus
from eval.metrics import percentile

STAGES = ("embed_ms", "search_ms", "prep_ms", "llm_ms", "total_ms")

_MARKER = re.compile(r"\[(\d+)\]")


def markers_in(answer_text: str | None) -> tuple[int, ...]:
    """The distinct `[n]` markers in an answer, in ascending order."""
    return tuple(sorted({int(number) for number in _MARKER.findall(answer_text or "")}))


@dataclass(frozen=True, slots=True)
class AnswerResult:
    """What the service recorded for one question."""

    question_id: str
    answerable: bool
    outcome: str
    answer_text: str | None
    markers: tuple[int, ...]
    cited_ranks: tuple[int, ...]
    invalid_citations: int | None
    error_class: str | None
    input_tokens: int | None
    output_tokens: int | None
    timings: dict[str, int | None]

    @property
    def unresolved_markers(self) -> tuple[int, ...]:
        """Markers in the returned answer that name no retrieved chunk marked as cited."""
        return tuple(marker for marker in self.markers if marker not in self.cited_ranks)


@dataclass(frozen=True, slots=True)
class AnsweringReport:
    results: tuple[AnswerResult, ...]

    @property
    def answerable(self) -> list[AnswerResult]:
        return [result for result in self.results if result.answerable]

    @property
    def unanswerable(self) -> list[AnswerResult]:
        return [result for result in self.results if not result.answerable]

    @property
    def refused(self) -> int:
        """Unanswerable questions recorded as insufficient evidence. Declined is not refused."""
        return sum(1 for r in self.unanswerable if r.outcome == "insufficient_evidence")

    @property
    def false_refusals(self) -> int:
        return sum(1 for r in self.answerable if r.outcome == "insufficient_evidence")

    def count(self, outcome: str) -> int:
        return sum(1 for result in self.results if result.outcome == outcome)

    @property
    def markers_checked(self) -> int:
        return sum(len(result.markers) for result in self.results)

    @property
    def unresolved_markers(self) -> int:
        return sum(len(result.unresolved_markers) for result in self.results)

    @property
    def questions_checked(self) -> int:
        """Questions whose answers reached citation validation."""
        return len(self._checked())

    @property
    def rejected_citations(self) -> int:
        return sum(result.invalid_citations or 0 for result in self._checked())

    @property
    def accepted_citations(self) -> int:
        return sum(len(result.cited_ranks) for result in self._checked())

    @property
    def raw_invalid_citation_rate(self) -> float | None:
        """Rejected citation numbers over rejected plus accepted; None if none were checked."""
        total = self.rejected_citations + self.accepted_citations
        return self.rejected_citations / total if total else None

    @property
    def input_tokens(self) -> int:
        return sum(result.input_tokens or 0 for result in self.results)

    @property
    def output_tokens(self) -> int:
        return sum(result.output_tokens or 0 for result in self.results)

    def latency(self, stage: str) -> dict[str, float | int | None]:
        """P50 and P95 of one stage's milliseconds, over the questions that reached it."""
        values = [ms for ms in (r.timings[stage] for r in self.results) if ms is not None]
        return {"p50": percentile(values, 50), "p95": percentile(values, 95), "n": len(values)}

    def _checked(self) -> list[AnswerResult]:
        return [result for result in self.results if result.invalid_citations is not None]


def run_answering(
    sessions: sessionmaker[Session],
    embedder: Embedder,
    generator: Generator,
    ingested: IngestedCorpus,
    golden: GoldenSet,
) -> AnsweringReport:
    """Ask every golden-set question through the answering path, then read what was recorded."""
    asked: list[tuple[str, bool, uuid.UUID]] = []
    for question_id, answerable, text in _questions(golden):
        with sessions() as session:
            recorded = answer_question(
                session,
                embedder,
                generator,
                collection_id=ingested.collection_id,
                user_id=ingested.user_id,
                text=text,
            )
            asked.append((question_id, answerable, recorded.question.id))

    with sessions() as session:
        return AnsweringReport(
            tuple(
                _read(session, question_id, answerable, row)
                for question_id, answerable, row in asked
            )
        )


def _questions(golden: GoldenSet) -> Iterator[tuple[str, bool, str]]:
    for answerable in golden.answerable:
        yield answerable.id, True, answerable.question
    for unanswerable in golden.unanswerable:
        yield unanswerable.id, False, unanswerable.question


def _read(session: Session, question_id: str, answerable: bool, row_id: uuid.UUID) -> AnswerResult:
    row = session.get_one(Question, row_id)
    cited = session.scalars(
        select(RetrievalResult.rank)
        .where(RetrievalResult.question_id == row_id, RetrievalResult.cited)
        .order_by(RetrievalResult.rank)
    ).all()
    return AnswerResult(
        question_id=question_id,
        answerable=answerable,
        outcome=row.outcome,
        answer_text=row.answer_text,
        markers=markers_in(row.answer_text),
        cited_ranks=tuple(cited),
        invalid_citations=row.invalid_citations,
        error_class=row.error_class,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        timings={stage: getattr(row, stage) for stage in STAGES},
    )
