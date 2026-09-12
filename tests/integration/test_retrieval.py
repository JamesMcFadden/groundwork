import math
import random
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import EMBEDDING_DIM, Chunk, Collection, Document
from app.db.session import build_engine, build_session_factory, database_ok
from app.retrieval.dense import TOP_K, dense_search
from app.retrieval.fulltext import fulltext_search
from app.retrieval.hybrid import hybrid_search
from app.services.embeddings import Embedder

HNSW_INDEX = "ix_chunks_embedding_hnsw"
GIN_INDEX = "ix_chunks_tsv_gin"


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


def near_axis(i: int, rng: random.Random) -> list[float]:
    """A unit vector a small random step away from one axis."""
    vector = [rng.gauss(0, 0.02) for _ in range(EMBEDDING_DIM)]
    vector[i] += 1.0
    length = math.sqrt(sum(x * x for x in vector))
    return [x / length for x in vector]


def price_out_all_but_index_scans(executor: Connection | Session) -> None:
    """Leave an ordered index scan as the only affordable plan, for this transaction.

    Test collections hold a handful of chunks, which the planner would rather read and
    sort directly, so the HNSW index would never be exercised.
    """
    for setting in ("enable_seqscan", "enable_bitmapscan", "enable_sort"):
        executor.execute(text(f"SET LOCAL {setting} = off"))


@contextmanager
def selects_sent(engine: Engine) -> Iterator[list[tuple[str, Any]]]:
    """Record the SELECT statements sent while the block runs."""
    sent: list[tuple[str, Any]] = []

    def record(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            sent.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield sent
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_search_can_use_the_hnsw_index(sessions: sessionmaker[Session]) -> None:
    """The index's operator class and the search's ordering must stay in step.

    An HNSW index serves only the distance its operator class defines. Ordered by any
    other, search silently goes back to comparing the question with every chunk.
    """
    collection_id = add_collection(sessions, "plan")
    engine: Engine = sessions.kw["bind"]
    with selects_sent(engine) as sent, sessions() as session:
        dense_search(session, collection_id, axis(0))

    assert len(sent) == 1
    statement, parameters = sent[0]
    with engine.connect() as connection:
        price_out_all_but_index_scans(connection)
        plan = connection.exec_driver_sql(f"EXPLAIN {statement}", parameters).scalars().all()
        connection.rollback()

    assert HNSW_INDEX in "\n".join(plan)


def test_a_small_collection_in_a_large_index_still_fills_its_results(
    sessions: sessionmaker[Session],
) -> None:
    """Iterative index scan: the collection filter no longer starves search of candidates.

    Every chunk of the crowding collection sits nearer the question than any of this
    one's. Stopping at the index's first 40 candidates, as HNSW does by default, all of
    them would belong to the crowd, and the filter would leave nothing to return.
    """
    mine = add_collection(sessions, "small")
    crowd = add_collection(sessions, "crowd")
    own = add_document(sessions, mine, [(f"own {i}", axis(i + 1)) for i in range(TOP_K)])
    rng = random.Random(0)
    add_document(sessions, crowd, [(f"crowd {i}", near_axis(0, rng)) for i in range(500)])

    with sessions() as session:
        price_out_all_but_index_scans(session)
        results = dense_search(session, mine, axis(0))

    assert sorted(result.chunk_id for result in results) == sorted(own)


def add_passages(
    sessions: sessionmaker[Session],
    collection_id: uuid.UUID,
    passages: list[str],
    filename: str = "report.pdf",
) -> list[int]:
    """Store passages for full-text search, which reads their words and never their vectors."""
    return add_document(
        sessions,
        collection_id,
        [(passage, axis(i)) for i, passage in enumerate(passages)],
        filename=filename,
    )


def test_full_text_finds_chunks_holding_only_some_of_the_questions_words(
    sessions: sessionmaker[Session],
) -> None:
    """Requiring every word would find nothing: no passage says "loud" or "people"."""
    collection_id = add_collection(sessions, "any-word")
    boom, _ = add_passages(
        sessions,
        collection_id,
        [
            "Supersonic aircraft produce a boom heard on the ground.",
            "Crew members reported fatigue on the space station.",
        ],
    )

    with sessions() as session:
        results = fulltext_search(
            session, collection_id, "How loud was the boom people heard on the ground?"
        )

    assert [result.chunk_id for result in results] == [boom]


def test_full_text_ranks_chunks_holding_more_of_the_questions_words_first(
    sessions: sessionmaker[Session],
) -> None:
    collection_id = add_collection(sessions, "full-text-ranking")
    one, three, _, two = add_passages(
        sessions,
        collection_id,
        [
            "A boom.",
            "The boom was heard on the ground.",
            "Nothing relevant here.",
            "The boom was heard.",
        ],
    )

    with sessions() as session:
        results = fulltext_search(session, collection_id, "Was the boom heard on the ground?")

    assert [result.chunk_id for result in results] == [three, two, one]
    assert results[0].score > results[1].score > results[2].score > 0


def test_full_text_searches_only_the_named_collection(sessions: sessionmaker[Session]) -> None:
    """Another collection's chunk holding every word of the question must not be returned."""
    mine = add_collection(sessions, "mine")
    other = add_collection(sessions, "other")
    (own,) = add_passages(sessions, mine, ["The boom was heard."])
    add_passages(sessions, other, ["The boom was heard on the ground."])

    with sessions() as session:
        results = fulltext_search(session, mine, "Was the boom heard on the ground?")

    assert [result.chunk_id for result in results] == [own]


def test_a_question_of_stopwords_alone_finds_nothing_by_full_text(
    sessions: sessionmaker[Session],
) -> None:
    collection_id = add_collection(sessions, "stopwords")
    add_passages(sessions, collection_id, ["What it is: a boom heard on the ground."])

    with sessions() as session:
        assert fulltext_search(session, collection_id, "What is it?") == []


def test_at_most_limit_chunks_are_returned_by_full_text(sessions: sessionmaker[Session]) -> None:
    collection_id = add_collection(sessions, "full-text-many")
    add_passages(sessions, collection_id, [f"boom {i}" for i in range(TOP_K + 2)])

    with sessions() as session:
        results = fulltext_search(session, collection_id, "boom")

    assert len(results) == TOP_K


def test_full_text_results_carry_what_a_citation_points_at(
    sessions: sessionmaker[Session],
) -> None:
    collection_id = add_collection(sessions, "full-text-citations")
    (chunk_id,) = add_passages(
        sessions, collection_id, ["the sonic boom"], filename="annual-report.pdf"
    )

    with sessions() as session:
        (result,) = fulltext_search(session, collection_id, "boom")
        chunk = session.get(Chunk, chunk_id)

    assert chunk is not None
    assert (result.chunk_id, result.document_id, result.filename) == (
        chunk_id,
        chunk.document_id,
        "annual-report.pdf",
    )
    assert (result.text, result.page_start, result.page_end) == ("the sonic boom", 1, 1)
    assert result.score > 0


def test_full_text_search_can_use_the_gin_index(sessions: sessionmaker[Session]) -> None:
    """Search must match `tsv` itself: an expression over `text` finds the same chunks, but
    recomputes every candidate's lexemes and leaves the index unused.

    A GIN index serves only bitmap scans, and while collections are small the planner reads
    them through the `collection_id` index instead. With that index dropped inside the
    transaction, and plain and index scans priced out, a bitmap scan of the GIN index is the
    only plan left that does not read the whole table.
    """
    collection_id = add_collection(sessions, "full-text-plan")
    engine: Engine = sessions.kw["bind"]
    with selects_sent(engine) as sent, sessions() as session:
        fulltext_search(session, collection_id, "boom heard")

    assert len(sent) == 1
    statement, parameters = sent[0]
    with engine.connect() as connection:
        # Undone by the rollback below, with the settings.
        connection.execute(text("DROP INDEX ix_chunks_collection"))
        for setting in ("enable_seqscan", "enable_indexscan"):
            connection.execute(text(f"SET LOCAL {setting} = off"))
        plan = connection.exec_driver_sql(f"EXPLAIN {statement}", parameters).scalars().all()
        connection.rollback()

    assert GIN_INDEX in "\n".join(plan)


def exact(session: Session) -> Session:
    """Make vector search in this transaction exact, by leaving it no index scan to use.

    HNSW is approximate. Over a table churned by earlier tests it can miss a true neighbour,
    as it did once on CI, and the tests below are about fusion, not the index's recall.
    """
    session.execute(text("SET LOCAL enable_indexscan = off"))
    return session


def leaning(similarity: float, i: int) -> list[float]:
    """A unit vector whose inner product with axis(0) is `similarity`, the rest along axis(i)."""
    vector = [0.0] * EMBEDDING_DIM
    vector[0] = similarity
    vector[i] = math.sqrt(1 - similarity**2)
    return vector


def test_hybrid_lifts_a_chunk_full_text_matches_above_those_dense_ranks_higher(
    sessions: sessionmaker[Session],
) -> None:
    collection_id = add_collection(sessions, "hybrid-lift")
    storage, jobs, boom = add_document(
        sessions,
        collection_id,
        [
            ("Uploads are kept in object storage.", axis(0)),
            ("Workers claim queued jobs.", between(0, 1)),
            ("The sonic boom was heard on the ground.", axis(2)),
        ],
    )
    question = "Was the boom heard on the ground?"

    with sessions() as session:
        dense = dense_search(exact(session), collection_id, axis(0))
    with sessions() as session:
        hybrid = hybrid_search(exact(session), collection_id, question, axis(0))

    assert [result.chunk_id for result in dense] == [storage, jobs, boom]
    assert [result.chunk_id for result in hybrid] == [boom, storage, jobs]


def test_hybrid_searches_only_the_named_collection(sessions: sessionmaker[Session]) -> None:
    """Another collection's chunk, nearest the question and holding its words, is not returned."""
    mine = add_collection(sessions, "mine")
    other = add_collection(sessions, "other")
    (own,) = add_document(sessions, mine, [("Workers claim queued jobs.", axis(1))])
    add_document(sessions, other, [("The sonic boom was heard on the ground.", axis(0))])

    with sessions() as session:
        results = hybrid_search(session, mine, "Was the boom heard on the ground?", axis(0))

    assert [result.chunk_id for result in results] == [own]


def test_a_chunk_sixth_in_both_searches_still_makes_the_top_five(
    sessions: sessionmaker[Session],
) -> None:
    """Each search is asked for candidates to a depth of its own, not the result limit.

    Five chunks lie nearer the question and hold none of its words; five others hold more
    of its words and lie further from it. Each search alone ranks sixth the chunk that is
    both near and matching, but a place in both lists lifts it into the top five, provided
    each search was asked for more than five.
    """
    collection_id = add_collection(sessions, "hybrid-depth")
    near = [(f"Stored upload number {i}.", leaning(0.9 - 0.05 * i, i + 1)) for i in range(TOP_K)]
    wordy = [("The boom was heard on the ground.", axis(10 + i)) for i in range(TOP_K)]
    chunk_ids = add_document(
        sessions, collection_id, [*near, ("A boom.", leaning(0.5, 20)), *wordy]
    )
    both = chunk_ids[TOP_K]
    question = "Was the boom heard on the ground?"

    with sessions() as session:
        dense = dense_search(exact(session), collection_id, axis(0), limit=TOP_K)
    with sessions() as session:
        full_text = fulltext_search(session, collection_id, question, limit=TOP_K)
    with sessions() as session:
        hybrid = hybrid_search(exact(session), collection_id, question, axis(0), limit=TOP_K)

    assert both not in [result.chunk_id for result in dense]
    assert both not in [result.chunk_id for result in full_text]
    assert both in [result.chunk_id for result in hybrid]
