import uuid
from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.jobs import claim_job, heartbeat
from app.db.models import Collection, Document, IngestionJob
from app.db.session import build_engine, build_session_factory, database_ok

STALE_AFTER = timedelta(minutes=5)
MAX_ATTEMPTS = 3


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
        collection = Collection(user_id=get_settings().default_user_id, name="jobs")
        session.add(collection)
        session.commit()
        return collection.id


def queue_job(sessions: sessionmaker[Session], collection_id: uuid.UUID) -> uuid.UUID:
    """Insert a document and its queued job, the way an upload does."""
    with sessions() as session:
        document = Document(
            collection_id=collection_id,
            filename="report.pdf",
            content_type="application/pdf",
            size_bytes=1024,
            s3_key=f"documents/{uuid.uuid4().hex}",
        )
        job = IngestionJob(document=document, status="queued")
        session.add_all([document, job])
        session.commit()
        return job.id


def go_stale(sessions: sessionmaker[Session], job_id: uuid.UUID) -> None:
    """Backdate the heartbeat past the staleness window, as a dead worker leaves it."""
    with sessions() as session:
        session.execute(
            text(
                "UPDATE ingestion_jobs SET heartbeat_at = now() - interval '10 minutes' "
                "WHERE id = :id"
            ),
            {"id": job_id},
        )
        session.commit()


def test_claims_a_queued_job_and_marks_it_running(
    sessions: sessionmaker[Session], collection_id: uuid.UUID
) -> None:
    job_id = queue_job(sessions, collection_id)

    with sessions() as session:
        job = claim_job(session, STALE_AFTER, MAX_ATTEMPTS)

    assert job is not None
    assert job.id == job_id
    assert job.status == "running"
    assert job.attempts == 1
    assert job.started_at is not None
    assert job.heartbeat_at is not None


def test_returns_none_when_nothing_is_queued(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        assert claim_job(session, STALE_AFTER, MAX_ATTEMPTS) is None


def test_a_claimed_job_is_not_handed_out_again(
    sessions: sessionmaker[Session], collection_id: uuid.UUID
) -> None:
    queue_job(sessions, collection_id)

    with sessions() as session:
        assert claim_job(session, STALE_AFTER, MAX_ATTEMPTS) is not None
    with sessions() as session:
        assert claim_job(session, STALE_AFTER, MAX_ATTEMPTS) is None


def test_claims_the_oldest_job_first(
    sessions: sessionmaker[Session], collection_id: uuid.UUID
) -> None:
    first = queue_job(sessions, collection_id)
    second = queue_job(sessions, collection_id)

    with sessions() as session:
        earlier = claim_job(session, STALE_AFTER, MAX_ATTEMPTS)
    with sessions() as session:
        later = claim_job(session, STALE_AFTER, MAX_ATTEMPTS)

    assert earlier is not None and earlier.id == first
    assert later is not None and later.id == second


def test_a_stale_job_is_reclaimed_and_the_attempt_counted(
    sessions: sessionmaker[Session], collection_id: uuid.UUID
) -> None:
    """A worker that dies holding a claim stops heartbeating; the row returns to the pool."""
    queue_job(sessions, collection_id)
    with sessions() as session:
        abandoned = claim_job(session, STALE_AFTER, MAX_ATTEMPTS)
    assert abandoned is not None
    go_stale(sessions, abandoned.id)

    with sessions() as session:
        reclaimed = claim_job(session, STALE_AFTER, MAX_ATTEMPTS)

    assert reclaimed is not None
    assert reclaimed.id == abandoned.id
    assert reclaimed.attempts == 2


def test_a_stale_job_with_no_attempts_left_is_not_reclaimed(
    sessions: sessionmaker[Session], collection_id: uuid.UUID
) -> None:
    """The attempt bound is what stops a document that kills its worker from cycling."""
    job_id = queue_job(sessions, collection_id)
    with sessions() as session:
        session.execute(
            text("UPDATE ingestion_jobs SET status = 'running', attempts = :n WHERE id = :id"),
            {"n": MAX_ATTEMPTS, "id": job_id},
        )
        session.commit()
    go_stale(sessions, job_id)

    with sessions() as session:
        assert claim_job(session, STALE_AFTER, MAX_ATTEMPTS) is None


def test_heartbeat_keeps_a_job_from_being_reclaimed(
    sessions: sessionmaker[Session], collection_id: uuid.UUID
) -> None:
    queue_job(sessions, collection_id)
    with sessions() as session:
        claimed = claim_job(session, STALE_AFTER, MAX_ATTEMPTS)
    assert claimed is not None
    go_stale(sessions, claimed.id)

    with sessions() as session:
        heartbeat(session, claimed.id)

    with sessions() as session:
        assert claim_job(session, STALE_AFTER, MAX_ATTEMPTS) is None
