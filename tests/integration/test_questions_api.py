import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.pool import QueuePool

from app.config import Settings, get_settings
from app.db.models import Chunk, Collection, Document, User
from app.db.session import build_engine, build_session_factory, database_ok
from app.generation.context import Passage
from app.generation.generator import Generation, GenerationDeclined, Generator, Statement
from app.generation.stub import StubGenerator
from app.main import create_app
from app.retrieval.hybrid import RRF_K, hybrid_search
from app.services.embeddings import Embedder

OTHER_USER_EMAIL = "questions-someone-else@example.com"
PASSAGES = [
    "Workers claim queued ingestion jobs and refresh a heartbeat while they run.",
    "A job whose heartbeat goes stale is reclaimed by another worker.",
    "Uploaded PDFs are kept in object storage under the SHA-256 hash of their bytes.",
]
QUESTION = "What happens to a job whose worker stops sending heartbeats?"


@pytest.fixture(autouse=True)
def engine() -> Iterator[Engine]:
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
            connection.execute(
                text("DELETE FROM users WHERE email = :email"), {"email": OTHER_USER_EMAIL}
            )

    clear()
    yield engine
    clear()


@contextmanager
def serving(
    embedder: Embedder, generator: Generator, settings: Settings | None = None
) -> Iterator[TestClient]:
    app = create_app(settings or get_settings(), embedder=embedder, generator=generator)
    with TestClient(app) as client:
        yield client


class FailingGenerator:
    """Raises instead of answering, as a timeout or a provider error would."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def generate(self, question: str, passages: Sequence[Passage]) -> Generation:
        raise self.error


class PoolWatchingGenerator(StubGenerator):
    """The stub, noting how many pooled connections are checked out while it answers."""

    def __init__(self) -> None:
        self.pool: QueuePool | None = None
        self.checked_out: list[int] = []

    def watch(self, engine: Engine) -> None:
        assert isinstance(engine.pool, QueuePool)
        self.pool = engine.pool

    def generate(self, question: str, passages: Sequence[Passage]) -> Generation:
        assert self.pool is not None
        self.checked_out.append(self.pool.checkedout())
        return super().generate(question, passages)


def add_collection(
    engine: Engine,
    embedder: Embedder,
    passages: Sequence[str],
    user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """A collection holding one document whose chunks are these passages, a page each."""
    with build_session_factory(engine)() as session:
        collection = Collection(
            user_id=user_id or get_settings().default_user_id,
            name=f"questions-{uuid.uuid4().hex[:8]}",
        )
        session.add(collection)
        session.flush()
        document = Document(
            collection=collection,
            filename="operations.pdf",
            content_type="application/pdf",
            size_bytes=1,
            s3_key=f"documents/{uuid.uuid4().hex}",
            page_count=len(passages),
        )
        embeddings = embedder.embed_passages(list(passages)) if passages else []
        session.add_all(
            [
                document,
                *(
                    Chunk(
                        document=document,
                        collection_id=collection.id,
                        chunk_index=index,
                        text=passage,
                        page_start=index + 1,
                        page_end=index + 1,
                        token_count=len(passage.split()),
                        embedding=embedding,
                    )
                    for index, (passage, embedding) in enumerate(
                        zip(passages, embeddings, strict=True)
                    )
                ),
            ]
        )
        session.commit()
        return collection.id


def ask(client: TestClient, collection_id: uuid.UUID, question: str = QUESTION) -> Any:
    return client.post(
        "/questions", json={"collection_id": str(collection_id), "question": question}
    )


def recorded_question(engine: Engine, collection_id: uuid.UUID) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT * FROM questions WHERE collection_id = :id"), {"id": collection_id}
        ).one()


def retrieval_results(engine: Engine, question_id: uuid.UUID | str) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                text(
                    "SELECT chunk_id, rank, score, cited FROM retrieval_results "
                    "WHERE question_id = :id ORDER BY rank"
                ),
                {"id": question_id},
            )
        )


def test_a_question_is_answered_with_citations_to_retrieved_chunks(
    engine: Engine, embedder: Embedder
) -> None:
    collection_id = add_collection(engine, embedder, PASSAGES)

    with serving(embedder, StubGenerator()) as client:
        response = ask(client, collection_id)

    assert response.status_code == 201
    body = response.json()
    assert (body["collection_id"], body["question"]) == (str(collection_id), QUESTION)
    assert body["outcome"] == "answered"
    assert "[1]" in body["answer"]
    results = retrieval_results(engine, body["id"])
    assert [(result.rank, result.cited) for result in results] == [
        (1, True),
        (2, False),
        (3, False),
    ]
    (citation,) = body["citations"]
    assert (citation["marker"], citation["chunk_id"]) == (1, results[0].chunk_id)
    assert citation["filename"] == "operations.pdf"
    assert citation["page_start"] == citation["page_end"]
    assert citation["score"] == pytest.approx(results[0].score)
    row = recorded_question(engine, collection_id)
    assert (row.id, row.outcome, row.answer_text) == (
        uuid.UUID(body["id"]),
        "answered",
        body["answer"],
    )
    assert (row.invalid_citations, row.error_class, row.retriever) == (0, None, "hybrid")


def test_the_configured_retriever_finds_the_chunks_and_is_recorded(
    engine: Engine, embedder: Embedder
) -> None:
    """With RETRIEVER=hybrid, the question is retrieved by hybrid search and recorded so.

    What is recorded must be exactly what hybrid search returns: fused scores, at most
    2 / (RRF_K + 1), where dense search's would be inner products.
    """
    collection_id = add_collection(engine, embedder, PASSAGES)
    settings = get_settings().model_copy(update={"retriever": "hybrid"})

    with serving(embedder, StubGenerator(), settings) as client:
        response = ask(client, collection_id)

    assert response.status_code == 201
    with build_session_factory(engine)() as session:
        expected = hybrid_search(session, collection_id, QUESTION, embedder.embed_query(QUESTION))
    results = retrieval_results(engine, response.json()["id"])
    assert [(result.chunk_id, result.score) for result in results] == [
        (chunk.chunk_id, pytest.approx(chunk.score)) for chunk in expected
    ]
    assert all(result.score <= 2 / (RRF_K + 1) for result in results)
    assert recorded_question(engine, collection_id).retriever == "hybrid"


def test_every_stage_is_timed_and_recorded_as_reported(engine: Engine, embedder: Embedder) -> None:
    collection_id = add_collection(engine, embedder, PASSAGES)

    with serving(embedder, StubGenerator()) as client:
        timings = ask(client, collection_id).json()["timings"]

    stages = ("embed_ms", "search_ms", "prep_ms", "llm_ms")
    assert all(isinstance(timings[name], int) and timings[name] >= 0 for name in timings)
    assert timings["total_ms"] >= max(timings[stage] for stage in stages)
    row = recorded_question(engine, collection_id)
    assert {name: getattr(row, name) for name in timings} == timings


def test_an_unknown_collection_returns_404(embedder: Embedder) -> None:
    with serving(embedder, StubGenerator()) as client:
        response = ask(client, uuid.uuid4())

    assert response.status_code == 404


def test_another_users_collection_is_indistinguishable_from_a_missing_one(
    engine: Engine, embedder: Embedder
) -> None:
    with build_session_factory(engine)() as session:
        user = User(email=OTHER_USER_EMAIL)
        session.add(user)
        session.commit()
        other_user_id = user.id
    theirs = add_collection(engine, embedder, PASSAGES, user_id=other_user_id)

    with serving(embedder, StubGenerator()) as client:
        their_response = ask(client, theirs)
        missing_response = ask(client, uuid.uuid4())

    assert their_response.status_code == 404
    assert their_response.json() == missing_response.json()
    with engine.connect() as connection:
        asked = connection.execute(
            text("SELECT count(*) FROM questions WHERE collection_id = :id"), {"id": theirs}
        ).scalar_one()
    assert asked == 0


@pytest.mark.parametrize("question", ["", "   ", "x" * 2001])
def test_an_empty_or_overlong_question_is_rejected(embedder: Embedder, question: str) -> None:
    with serving(embedder, StubGenerator()) as client:
        response = ask(client, uuid.uuid4(), question)

    assert response.status_code == 422


def test_a_failed_generation_is_recorded_and_reported_as_a_generic_502(
    engine: Engine, embedder: Embedder
) -> None:
    """The caller learns nothing of the cause; the record keeps its class name only."""
    collection_id = add_collection(engine, embedder, PASSAGES)
    error = TimeoutError("provider at 10.0.0.7 did not answer")

    with serving(embedder, FailingGenerator(error)) as client:
        response = ask(client, collection_id)

    assert (response.status_code, response.json()) == (502, {"detail": "answer generation failed"})
    row = recorded_question(engine, collection_id)
    assert (row.outcome, row.error_class) == ("failed", "TimeoutError")
    assert (row.answer_text, row.invalid_citations) == (None, None)
    assert None not in (row.embed_ms, row.search_ms, row.prep_ms, row.llm_ms, row.total_ms)
    assert [result.cited for result in retrieval_results(engine, row.id)] == [False] * 3


def test_a_declined_generation_is_recorded_as_declined_with_its_tokens(
    engine: Engine, embedder: Embedder
) -> None:
    collection_id = add_collection(engine, embedder, PASSAGES)
    declined = GenerationDeclined("the model declined to answer", 812, 9)

    with serving(embedder, FailingGenerator(declined)) as client:
        response = ask(client, collection_id)

    assert response.status_code == 502
    row = recorded_question(engine, collection_id)
    assert (row.outcome, row.error_class) == ("declined", "GenerationDeclined")
    assert (row.input_tokens, row.output_tokens, row.answer_text) == (812, 9, None)


def test_no_database_connection_is_held_while_the_model_generates(
    engine: Engine, embedder: Embedder
) -> None:
    """A slow generation must not pin a pooled connection for its whole duration."""
    collection_id = add_collection(engine, embedder, PASSAGES)
    watcher = PoolWatchingGenerator()
    app = create_app(get_settings(), embedder=embedder, generator=watcher)
    watcher.watch(app.state.engine)

    with TestClient(app) as client:
        response = ask(client, collection_id)

    assert response.status_code == 201
    assert watcher.checked_out == [0]


class ScriptedGenerator:
    """Answers with fixed statements whatever it is asked, citing what the test chooses."""

    def __init__(self, *statements: Statement) -> None:
        self.statements = statements

    def generate(self, question: str, passages: Sequence[Passage]) -> Generation:
        return Generation(
            statements=self.statements,
            insufficient_evidence=False,
            input_tokens=500,
            output_tokens=60,
        )


def test_invalid_citations_are_never_returned_or_recorded(
    engine: Engine, embedder: Embedder
) -> None:
    """Three passages are supplied, so [7] and [0] name nothing.

    They must vanish from the answer, its citations, and the record alike, and a statement
    resting only on them must go with them. What remains of them is a count.
    """
    collection_id = add_collection(engine, embedder, PASSAGES)
    generator = ScriptedGenerator(
        Statement("Stale jobs are reclaimed by another worker.", (2, 7)),
        Statement("Reclaimed jobs are retried forever.", (0,)),
    )

    with serving(embedder, generator) as client:
        body = ask(client, collection_id).json()

    assert body["outcome"] == "answered"
    assert body["answer"] == "Stale jobs are reclaimed by another worker [2]."
    assert [citation["marker"] for citation in body["citations"]] == [2]
    results = retrieval_results(engine, body["id"])
    assert [(result.rank, result.cited) for result in results] == [
        (1, False),
        (2, True),
        (3, False),
    ]
    assert body["citations"][0]["chunk_id"] == results[1].chunk_id
    row = recorded_question(engine, collection_id)
    assert (row.answer_text, row.invalid_citations) == (body["answer"], 2)


def test_an_answer_left_with_no_valid_citation_is_downgraded_to_insufficient_evidence(
    engine: Engine, embedder: Embedder
) -> None:
    """Nothing the answer claims rests on a supplied passage, so nothing of it is returned."""
    collection_id = add_collection(engine, embedder, PASSAGES)
    generator = ScriptedGenerator(
        Statement("An invented claim.", (4,)),
        Statement("Another invented claim.", (9, 4)),
    )

    with serving(embedder, generator) as client:
        response = ask(client, collection_id)

    assert response.status_code == 201
    body = response.json()
    assert (body["outcome"], body["answer"], body["citations"]) == (
        "insufficient_evidence",
        None,
        [],
    )
    assert not any(result.cited for result in retrieval_results(engine, body["id"]))
    row = recorded_question(engine, collection_id)
    assert (row.outcome, row.answer_text, row.invalid_citations) == (
        "insufficient_evidence",
        None,
        2,
    )


class RecordingGenerator(StubGenerator):
    """The stub, keeping every question it is asked."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def generate(self, question: str, passages: Sequence[Passage]) -> Generation:
        self.asked.append(question)
        return super().generate(question, passages)


def test_insufficient_evidence_skips_the_model_call(engine: Engine, embedder: Embedder) -> None:
    """With nothing retrieved there is nothing to answer from, and nothing to pay for.

    Another collection holds passages that match the question closely; the one asked holds
    none, so the question is recorded as insufficient evidence without the model.
    """
    add_collection(engine, embedder, PASSAGES)
    empty = add_collection(engine, embedder, [])
    generator = RecordingGenerator()

    with serving(embedder, generator) as client:
        response = ask(client, empty)

    assert generator.asked == []
    assert response.status_code == 201
    body = response.json()
    assert (body["outcome"], body["answer"], body["citations"]) == (
        "insufficient_evidence",
        None,
        [],
    )
    timings = body["timings"]
    assert (timings["prep_ms"], timings["llm_ms"]) == (None, None)
    assert None not in (timings["embed_ms"], timings["search_ms"], timings["total_ms"])
    row = recorded_question(engine, empty)
    assert (row.outcome, row.invalid_citations, row.input_tokens, row.error_class) == (
        "insufficient_evidence",
        None,
        None,
        None,
    )
    assert retrieval_results(engine, body["id"]) == []
