import re
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import EMBEDDING_DIM, Chunk, Collection, Document, User
from app.db.session import build_engine, build_session_factory, database_ok
from app.services.embeddings import Embedder
from eval.corpus import load_corpus
from eval.golden import AnswerableQuestion, Evidence, GoldenSet, load_golden, normalise
from eval.ingest import IngestedCorpus, ingest_corpus
from eval.results import pgvector_version
from eval.retrieval import MRR_K, RECALL_K, run_retrieval

# Not the eval user: a test run must never replace a developer's eval collection.
TEST_EMAIL = "eval-retrieval-tests@example.com"
SMALL_REPORT = "uam-risk.pdf"


@pytest.fixture
def sessions() -> Iterator[sessionmaker[Session]]:
    engine: Engine = build_engine(get_settings())
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


def test_every_question_is_scored_from_the_services_search(
    sessions: sessionmaker[Session],
    embedder: Embedder,
    ingested: IngestedCorpus,
    golden: GoldenSet,
) -> None:
    report = run_retrieval(sessions, embedder.embed_query, ingested.collection_id, golden, "dense")

    assert [r.question_id for r in report.answerable] == [q.id for q in golden.answerable]
    assert [r.question_id for r in report.unanswerable] == [q.id for q in golden.unanswerable]
    for result in report.answerable:
        assert result.rank_at_5 is None or 1 <= result.rank_at_5 <= RECALL_K
        assert result.rank_at_10 is None or 1 <= result.rank_at_10 <= MRR_K
    scores = [r.best_score for r in report.answerable] + [r.best_score for r in report.unanswerable]
    for score in scores:
        assert score is not None and -1.0 <= score <= 1.0 + 1e-6


def test_a_question_embedded_as_its_answers_chunk_ranks_that_chunk_first(
    sessions: sessionmaker[Session], ingested: IngestedCorpus, golden: GoldenSet
) -> None:
    """End to end over real chunks: the hit rule recognises the chunk that holds the quote."""
    question = golden.answerable[0]
    (evidence,) = question.evidence
    with sessions() as session:
        holding = next(
            chunk
            for chunk in session.scalars(
                select(Chunk).where(Chunk.collection_id == ingested.collection_id)
            )
            if chunk.page_start <= evidence.page <= chunk.page_end
            and normalise(evidence.quote) in normalise(chunk.text)
        )
        vector = [float(x) for x in holding.embedding]

    single = GoldenSet(answerable=(question,), unanswerable=(), sha256="")
    report = run_retrieval(sessions, lambda _: vector, ingested.collection_id, single, "dense")

    (result,) = report.answerable
    assert (result.rank_at_5, result.rank_at_10) == (1, 1)
    assert result.best_score == pytest.approx(1.0, abs=1e-3)
    assert report.recall_at_5 == 1.0


def axis(i: int) -> list[float]:
    """A unit vector along one axis; its inner product with any other axis is zero."""
    vector = [0.0] * EMBEDDING_DIM
    vector[i] = 1.0
    return vector


def test_questions_are_scored_from_the_search_the_retriever_names(
    sessions: sessionmaker[Session],
) -> None:
    """Dense search ranks the answer third here, and hybrid search ranks it first.

    A harness that searched one way whatever it was told would score one of them wrongly.
    Only the answer holds the question's words; the question's vector matches another chunk.
    """
    passages = [
        ("Uploads are kept in object storage.", axis(0)),
        ("Workers claim queued jobs.", axis(1)),
        ("The sonic boom was heard on the ground.", axis(2)),
    ]
    with sessions() as session:
        user = User(email=TEST_EMAIL)
        session.add(user)
        session.flush()
        collection = Collection(user_id=user.id, name="retriever-scoring")
        document = Document(
            collection=collection,
            filename="report.pdf",
            content_type="application/pdf",
            size_bytes=1,
            s3_key="documents/retriever-scoring",
            page_count=len(passages),
        )
        session.add_all([collection, document])
        session.flush()
        session.add_all(
            Chunk(
                document=document,
                collection_id=collection.id,
                chunk_index=index,
                text=passage,
                page_start=index + 1,
                page_end=index + 1,
                token_count=len(passage.split()),
                embedding=vector,
            )
            for index, (passage, vector) in enumerate(passages)
        )
        session.commit()
        collection_id = collection.id
    question = AnswerableQuestion(
        id="a01",
        question="Was the boom heard on the ground?",
        evidence=(Evidence(document="report.pdf", page=3, quote="sonic boom was heard"),),
    )
    golden = GoldenSet(answerable=(question,), unanswerable=(), sha256="")

    dense = run_retrieval(sessions, lambda _: axis(0), collection_id, golden, "dense")
    hybrid = run_retrieval(sessions, lambda _: axis(0), collection_id, golden, "hybrid")

    assert (dense.retriever, hybrid.retriever) == ("dense", "hybrid")
    assert [(r.rank_at_5, r.rank_at_10) for r in dense.answerable] == [(3, 3)]
    assert [(r.rank_at_5, r.rank_at_10) for r in hybrid.answerable] == [(1, 1)]
    assert (dense.answerable[0].best_score, hybrid.answerable[0].best_score) == (
        pytest.approx(1.0),
        None,
    )


def test_the_pgvector_version_is_read_from_the_database(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        version = pgvector_version(session)

    assert version is not None and re.fullmatch(r"\d+\.\d+\.\d+", version)
