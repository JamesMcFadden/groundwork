from collections.abc import Iterator, Sequence

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Question
from app.db.session import build_engine, build_session_factory, database_ok
from app.generation.context import Passage
from app.generation.generator import Generation, GenerationDeclined, Statement
from app.generation.stub import StubGenerator
from app.services.embeddings import Embedder
from eval.answering import run_answering
from eval.corpus import load_corpus
from eval.golden import GoldenSet, load_golden
from eval.ingest import IngestedCorpus, ingest_corpus

# Not the eval user: a test run must never replace a developer's eval collection.
TEST_EMAIL = "eval-answering-tests@example.com"
SMALL_REPORT = "uam-risk.pdf"


class ScriptedGenerator:
    """Refuses some questions, declines one, and cites a passage never supplied for the rest."""

    def __init__(self, refuse: set[str], decline: str) -> None:
        self.refuse = refuse
        self.decline = decline

    def generate(self, question: str, passages: Sequence[Passage]) -> Generation:
        if question in self.refuse:
            return Generation(statements=(), insufficient_evidence=True)
        if question == self.decline:
            raise GenerationDeclined("declined on safety grounds")
        return Generation(
            statements=(Statement(text="A claim from the passages.", citations=(1, 9)),),
            insufficient_evidence=False,
        )


@pytest.fixture
def sessions() -> Iterator[sessionmaker[Session]]:
    engine = build_engine(get_settings())
    if not database_ok(engine):
        pytest.skip("database unavailable; start it with `docker compose up -d postgres`")

    def clear() -> None:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM users WHERE email = :email"), {"email": TEST_EMAIL}
            )

    clear()
    yield build_session_factory(engine)
    clear()


@pytest.fixture
def golden() -> GoldenSet:
    """The real questions about the small report, and two unanswerable ones."""
    corpus = load_corpus()
    full = load_golden([document.filename for document in corpus.documents])
    return GoldenSet(
        answerable=tuple(q for q in full.answerable if q.evidence[0].document == SMALL_REPORT),
        unanswerable=full.unanswerable[:2],
        sha256=full.sha256,
    )


@pytest.fixture
def ingested(sessions: sessionmaker[Session], embedder: Embedder) -> IngestedCorpus:
    documents = [d for d in load_corpus().documents if d.filename == SMALL_REPORT]
    return ingest_corpus(sessions, embedder, documents, email=TEST_EMAIL)


def test_answers_are_scored_from_what_the_service_recorded(
    sessions: sessionmaker[Session],
    embedder: Embedder,
    ingested: IngestedCorpus,
    golden: GoldenSet,
) -> None:
    declined = golden.answerable[0]
    generator = ScriptedGenerator(
        refuse={question.question for question in golden.unanswerable},
        decline=declined.question,
    )

    report = run_answering(sessions, embedder, generator, ingested, golden, "dense")

    with sessions() as session:
        recorded = session.scalar(
            select(func.count())
            .select_from(Question)
            .where(Question.collection_id == ingested.collection_id)
        )
    assert recorded == len(golden.answerable) + len(golden.unanswerable)

    assert (report.refused, len(report.unanswerable)) == (2, 2)
    assert report.false_refusals == 0
    assert report.count("declined") == 1
    assert next(r for r in report.results if r.question_id == declined.id).error_class == (
        "GenerationDeclined"
    )
    # Four answers each cited passages 1 and 9; only five passages were supplied, so 9 was
    # rejected every time and never reached the returned text.
    assert (report.markers_checked, report.unresolved_markers) == (4, 0)
    assert (report.rejected_citations, report.accepted_citations) == (4, 4)
    assert report.questions_checked == 6
    assert report.raw_invalid_citation_rate == pytest.approx(0.5)
    assert all(result.timings["total_ms"] is not None for result in report.results)


def test_the_stub_answers_every_question_from_its_top_passage(
    sessions: sessionmaker[Session],
    embedder: Embedder,
    ingested: IngestedCorpus,
    golden: GoldenSet,
) -> None:
    report = run_answering(sessions, embedder, StubGenerator(), ingested, golden, "dense")

    assert {result.outcome for result in report.results} == {"answered"}
    assert all(result.cited_ranks == (1,) == result.markers for result in report.results)
    assert report.refused == 0
    assert report.unresolved_markers == 0


def test_each_question_is_answered_with_the_named_retriever_and_recorded_so(
    sessions: sessionmaker[Session],
    embedder: Embedder,
    ingested: IngestedCorpus,
    golden: GoldenSet,
) -> None:
    report = run_answering(sessions, embedder, StubGenerator(), ingested, golden, "hybrid")

    assert len(report.results) == len(golden.answerable) + len(golden.unanswerable)
    assert {result.retriever for result in report.results} == {"hybrid"}
