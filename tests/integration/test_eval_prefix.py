from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.session import build_engine, build_session_factory, database_ok
from app.services.embeddings import Embedder
from eval.corpus import load_corpus
from eval.golden import GoldenSet, load_golden
from eval.ingest import ingest_corpus
from eval.prefix import compare_prefix
from eval.retrieval import run_retrieval

# Not the eval user: a test run must never replace a developer's eval collection.
TEST_EMAIL = "eval-prefix-tests@example.com"
SMALL_REPORT = "uam-risk.pdf"


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


def test_the_comparison_starts_from_the_services_own_retrieval_and_the_prefix_changes_it(
    sessions: sessionmaker[Session], embedder: Embedder
) -> None:
    """Without the prefix, questions must retrieve exactly as the service retrieves them.

    Otherwise the comparison would measure a difference between two embeddings neither of
    which the service uses.
    """
    corpus = load_corpus()
    full = load_golden([document.filename for document in corpus.documents])
    golden = GoldenSet(
        answerable=tuple(q for q in full.answerable if q.evidence[0].document == SMALL_REPORT),
        unanswerable=full.unanswerable[:2],
        sha256=full.sha256,
    )
    documents = [d for d in corpus.documents if d.filename == SMALL_REPORT]
    ingested = ingest_corpus(sessions, embedder, documents, email=TEST_EMAIL)

    comparison = compare_prefix(sessions, embedder, ingested.collection_id, golden)
    service = run_retrieval(sessions, embedder.embed_query, ingested.collection_id, golden)

    assert comparison.without == service
    without_scores = [r.best_score for r in comparison.without.answerable]
    with_scores = [r.best_score for r in comparison.with_prefix.answerable]
    assert with_scores != without_scores
