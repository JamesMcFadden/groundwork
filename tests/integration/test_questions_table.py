import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Collection, Question
from app.db.session import build_engine, build_session_factory, database_ok

OUTCOMES = ("answered", "insufficient_evidence", "declined", "failed")
RETRIEVERS = ("dense", "hybrid")


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


@pytest.fixture
def collection_id(sessions: sessionmaker[Session]) -> uuid.UUID:
    with sessions() as session:
        collection = Collection(user_id=get_settings().default_user_id, name="questions")
        session.add(collection)
        session.commit()
        return collection.id


def record(sessions: sessionmaker[Session], collection_id: uuid.UUID, **fields: Any) -> None:
    with sessions() as session:
        session.add(
            Question(
                collection_id=collection_id,
                user_id=get_settings().default_user_id,
                question_text="How is an abandoned job recovered?",
                **{"retriever": "dense", **fields},
            )
        )
        session.commit()


def test_each_outcome_can_be_recorded(
    sessions: sessionmaker[Session], collection_id: uuid.UUID
) -> None:
    for outcome in OUTCOMES:
        record(sessions, collection_id, outcome=outcome)

    with sessions() as session:
        stored = session.scalars(
            select(Question.outcome).where(Question.collection_id == collection_id)
        ).all()

    assert sorted(stored) == sorted(OUTCOMES)


def test_a_failed_question_keeps_its_error_class_and_the_timings_of_stages_that_ran(
    sessions: sessionmaker[Session], collection_id: uuid.UUID
) -> None:
    record(
        sessions,
        collection_id,
        outcome="failed",
        error_class="APITimeoutError",
        embed_ms=11,
        search_ms=4,
        prep_ms=1,
    )

    with sessions() as session:
        question = session.scalars(
            select(Question).where(Question.collection_id == collection_id)
        ).one()

    assert (question.outcome, question.error_class) == ("failed", "APITimeoutError")
    assert (question.embed_ms, question.search_ms, question.prep_ms) == (11, 4, 1)
    assert (question.answer_text, question.invalid_citations, question.llm_ms) == (
        None,
        None,
        None,
    )


def test_an_unknown_outcome_is_rejected(
    sessions: sessionmaker[Session], collection_id: uuid.UUID
) -> None:
    """Evaluation groups by outcome; a misspelt value would fall out of every rate."""
    with pytest.raises(IntegrityError, match="ck_questions_outcome"):
        record(sessions, collection_id, outcome="refused")


def test_a_question_cannot_be_recorded_without_an_outcome(
    sessions: sessionmaker[Session], collection_id: uuid.UUID
) -> None:
    with pytest.raises(IntegrityError, match='"outcome"'):
        record(sessions, collection_id)


def test_each_retriever_can_be_recorded(
    sessions: sessionmaker[Session], collection_id: uuid.UUID
) -> None:
    for retriever in RETRIEVERS:
        record(sessions, collection_id, outcome="answered", retriever=retriever)

    with sessions() as session:
        stored = session.scalars(
            select(Question.retriever).where(Question.collection_id == collection_id)
        ).all()

    assert sorted(stored) == sorted(RETRIEVERS)


def test_an_unknown_retriever_is_rejected(
    sessions: sessionmaker[Session], collection_id: uuid.UUID
) -> None:
    """A recorded score means nothing without the search that produced it."""
    with pytest.raises(IntegrityError, match="ck_questions_retriever"):
        record(sessions, collection_id, outcome="answered", retriever="sparse")


def test_a_question_cannot_be_recorded_without_a_retriever(
    sessions: sessionmaker[Session], collection_id: uuid.UUID
) -> None:
    with pytest.raises(IntegrityError, match='"retriever"'):
        record(sessions, collection_id, outcome="answered", retriever=None)
