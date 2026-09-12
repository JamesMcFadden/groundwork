import math
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import EMBEDDING_DIM, Chunk, Collection, Document
from app.db.session import build_engine, build_session_factory, database_ok
from app.retrieval.dense import TOP_K, dense_search
from app.services.embeddings import Embedder


@pytest.fixture
def sessions() -> Iterator[sessionmaker[Session]]:
    settings = get_settings()
    engine = build_engine(settings)
    if not database_ok(engine):
        pytest.skip("database unavailable; start it with `docker compose up -d postgres`")

    def clear() -> None:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM collections WHERE user_id = :user_id"),
                {"user_id": settings.default_user_id},
            )

    clear()
    yield build_session_factory(engine)
    clear()


def axis(i: int) -> list[float]:
    """A unit vector along one axis; its inner product with any other axis is zero."""
    vector = [0.0] * EMBEDDING_DIM
    vector[i] = 1.0
    return vector


def between(i: int, j: int) -> list[float]:
    """A unit vector halfway between two axes."""
    vector = [0.0] * EMBEDDING_DIM
    vector[i] = vector[j] = 1 / math.sqrt(2)
    return vector


def add_collection(sessions: sessionmaker[Session], name: str) -> uuid.UUID:
    with sessions() as session:
        collection = Collection(user_id=get_settings().default_user_id, name=name)
        session.add(collection)
        session.commit()
        return collection.id


def add_document(
    sessions: sessionmaker[Session],
    collection_id: uuid.UUID,
    passages: list[tuple[str, list[float]]],
    filename: str = "report.pdf",
) -> list[int]:
    """Store a document with one chunk per passage, one page each; return the chunk ids."""
    with sessions() as session:
        document = Document(
            collection_id=collection_id,
            filename=filename,
            content_type="application/pdf",
            size_bytes=1,
            s3_key=f"documents/{uuid.uuid4().hex}",
            page_count=len(passages),
        )
        chunks = [
            Chunk(
                document=document,
                collection_id=collection_id,
                chunk_index=index,
                text=passage,
                page_start=index + 1,
                page_end=index + 1,
                token_count=len(passage.split()),
                embedding=embedding,
            )
            for index, (passage, embedding) in enumerate(passages)
        ]
        session.add_all([document, *chunks])
        session.commit()
        return [chunk.id for chunk in chunks]


def test_the_nearest_chunks_come_first_scored_by_inner_product(
    sessions: sessionmaker[Session],
) -> None:
    collection_id = add_collection(sessions, "ranking")
    far, near, exact = add_document(
        sessions, collection_id, [("far", axis(1)), ("near", between(0, 1)), ("exact", axis(0))]
    )

    with sessions() as session:
        results = dense_search(session, collection_id, axis(0))

    assert [result.chunk_id for result in results] == [exact, near, far]
    assert [round(result.score, 3) for result in results] == [1.0, 0.707, 0.0]


def test_only_the_named_collection_is_searched(sessions: sessionmaker[Session]) -> None:
    """Another collection's chunk matching the question exactly must not be returned."""
    mine = add_collection(sessions, "mine")
    other = add_collection(sessions, "other")
    (own,) = add_document(sessions, mine, [("own", axis(1))])
    add_document(sessions, other, [("theirs", axis(0))])

    with sessions() as session:
        results = dense_search(session, mine, axis(0))

    assert [result.chunk_id for result in results] == [own]


def test_at_most_top_k_chunks_are_returned(sessions: sessionmaker[Session]) -> None:
    collection_id = add_collection(sessions, "many")
    add_document(sessions, collection_id, [(f"passage {i}", axis(i)) for i in range(TOP_K + 2)])

    with sessions() as session:
        results = dense_search(session, collection_id, axis(0))

    assert len(results) == TOP_K


def test_a_collection_without_chunks_returns_nothing(sessions: sessionmaker[Session]) -> None:
    collection_id = add_collection(sessions, "empty")

    with sessions() as session:
        assert dense_search(session, collection_id, axis(0)) == []


def test_results_carry_what_a_citation_points_at(sessions: sessionmaker[Session]) -> None:
    collection_id = add_collection(sessions, "citations")
    (chunk_id,) = add_document(
        sessions, collection_id, [("the passage", axis(0))], filename="annual-report.pdf"
    )

    with sessions() as session:
        (result,) = dense_search(session, collection_id, axis(0))
        chunk = session.get(Chunk, chunk_id)

    assert chunk is not None
    assert (result.chunk_id, result.document_id, result.filename) == (
        chunk_id,
        chunk.document_id,
        "annual-report.pdf",
    )
    assert (result.text, result.page_start, result.page_end) == ("the passage", 1, 1)


def test_a_question_finds_the_passage_that_answers_it(
    sessions: sessionmaker[Session], embedder: Embedder
) -> None:
    """Through the real model: questions and passages are embedded into one space."""
    passages = [
        "Workers claim queued ingestion jobs and refresh a heartbeat while they run.",
        "Uploaded PDFs are kept in object storage, under the SHA-256 hash of their bytes.",
        "A monthly spend limit caps what a workspace can be billed for model usage.",
    ]
    collection_id = add_collection(sessions, "semantic")
    ids = add_document(
        sessions,
        collection_id,
        list(zip(passages, embedder.embed_passages(passages), strict=True)),
    )

    with sessions() as session:
        results = dense_search(
            session, collection_id, embedder.embed_query("Where is an uploaded file stored?")
        )

    assert results[0].chunk_id == ids[1]
