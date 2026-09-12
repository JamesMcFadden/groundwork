from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Chunk, Collection, Document, User
from app.db.session import build_engine, build_session_factory, database_ok
from app.services.embeddings import Embedder
from eval.corpus import CorpusDocument, load_corpus
from eval.ingest import COLLECTION_NAME, ingest_corpus

# Not the eval user: a test run must never delete a developer's eval collection.
TEST_EMAIL = "eval-ingest-tests@example.com"
DEFAULT_USER_COLLECTION = "eval-ingest-default-user"

# The shortest report, so each ingest embeds a few dozen chunks rather than hundreds.
SMALL_REPORT = "uam-risk.pdf"


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = build_engine(get_settings())
    if not database_ok(engine):
        pytest.skip("database unavailable; start it with `docker compose up -d postgres`")

    def clear() -> None:
        with engine.begin() as connection:
            # Deleting the user cascades to its collections, documents, and chunks.
            connection.execute(
                text("DELETE FROM users WHERE email = :email"), {"email": TEST_EMAIL}
            )
            connection.execute(
                text("DELETE FROM collections WHERE user_id = :user_id AND name = :name"),
                {"user_id": get_settings().default_user_id, "name": DEFAULT_USER_COLLECTION},
            )

    clear()
    yield engine
    clear()


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    return build_session_factory(engine)


@pytest.fixture(scope="module")
def small_report() -> list[CorpusDocument]:
    return [document for document in load_corpus().documents if document.filename == SMALL_REPORT]


def test_the_corpus_is_indexed_into_a_collection_of_its_own(
    sessions: sessionmaker[Session], embedder: Embedder, small_report: list[CorpusDocument]
) -> None:
    ingested = ingest_corpus(sessions, embedder, small_report, email=TEST_EMAIL)

    with sessions() as session:
        collection = session.get_one(Collection, ingested.collection_id)
        owner = session.get_one(User, collection.user_id)
        (document,) = session.scalars(
            select(Document).where(Document.collection_id == collection.id)
        ).all()
        chunks = session.scalars(select(Chunk).where(Chunk.document_id == document.id)).all()

    assert (owner.email, collection.name) == (TEST_EMAIL, COLLECTION_NAME)
    assert document.filename == SMALL_REPORT
    assert document.page_count == len(ingested.pages[SMALL_REPORT]) == 17
    assert len(chunks) == ingested.chunk_counts[SMALL_REPORT] > 0
    assert all(chunk.collection_id == collection.id for chunk in chunks)
    assert all(1 <= chunk.page_start <= chunk.page_end <= 17 for chunk in chunks)


def test_ingesting_again_replaces_the_previous_collection(
    sessions: sessionmaker[Session], embedder: Embedder, small_report: list[CorpusDocument]
) -> None:
    """Runs stay comparable only if the index holds one copy of the corpus, not one per run."""
    first = ingest_corpus(sessions, embedder, small_report, email=TEST_EMAIL)
    second = ingest_corpus(sessions, embedder, small_report, email=TEST_EMAIL)

    with sessions() as session:
        collections = session.scalars(
            select(Collection.id).where(Collection.user_id == second.user_id)
        ).all()
        chunk_count = session.scalar(
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.collection_id == first.collection_id)
        )

    assert collections == [second.collection_id]
    assert chunk_count == 0
    assert second.chunk_counts == first.chunk_counts


def test_another_users_collections_are_left_alone(
    sessions: sessionmaker[Session], embedder: Embedder, small_report: list[CorpusDocument]
) -> None:
    """Replacing eval collections must never reach the collections the service serves."""
    with sessions() as session:
        theirs = Collection(user_id=get_settings().default_user_id, name=DEFAULT_USER_COLLECTION)
        session.add(theirs)
        session.commit()
        theirs_id = theirs.id

    ingest_corpus(sessions, embedder, small_report, email=TEST_EMAIL)

    with sessions() as session:
        assert session.get(Collection, theirs_id) is not None
