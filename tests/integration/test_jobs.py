import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.jobs import claim_job, complete_job, expire_exhausted, fail_job, heartbeat
from app.db.models import Collection, Document, IngestionJob
from app.db.session import build_engine, build_session_factory, database_ok

STALE_AFTER = timedelta(minutes=5)
MAX_ATTEMPTS = 3
CONCURRENT_WORKERS = 8
CONCURRENT_JOBS = 40


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


def status_of(sessions: sessionmaker[Session], job_id: uuid.UUID) -> tuple[str, str | None, bool]:
    """Return a job's status, its error, and whether it has a finish time."""
    with sessions() as session:
        row = session.execute(
            text(
                "SELECT status, error, finished_at IS NOT NULL AS finished "
                "FROM ingestion_jobs WHERE id = :id"
            ),
            {"id": job_id},
        ).one()
        return row.status, row.error, row.finished


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
        assert heartbeat(session, claimed.id, claimed.attempts)

    with sessions() as session:
        assert claim_job(session, STALE_AFTER, MAX_ATTEMPTS) is None


def test_heartbeat_reports_a_claim_lost_to_a_reclaim(
    sessions: sessionmaker[Session], collection_id: uuid.UUID
) -> None:
    queue_job(sessions, collection_id)
    with sessions() as session:
        stalled = claim_job(session, STALE_AFTER, MAX_ATTEMPTS)
    assert stalled is not None
    go_stale(sessions, stalled.id)
    with sessions() as session:
        assert claim_job(session, STALE_AFTER, MAX_ATTEMPTS) is not None

    with sessions() as session:
        assert heartbeat(session, stalled.id, stalled.attempts) is False


def test_completion_is_refused_to_a_worker_whose_claim_was_reclaimed(
    sessions: sessionmaker[Session], collection_id: uuid.UUID
) -> None:
    """`attempts` fences the stalled worker out; only the current holder may finish."""
    queue_job(sessions, collection_id)
    with sessions() as session:
        stalled = claim_job(session, STALE_AFTER, MAX_ATTEMPTS)
    assert stalled is not None
    go_stale(sessions, stalled.id)
    with sessions() as session:
        current = claim_job(session, STALE_AFTER, MAX_ATTEMPTS)
    assert current is not None

    with sessions() as session:
        assert complete_job(session, stalled.id, stalled.attempts) is False
        assert complete_job(session, current.id, current.attempts) is True
        session.commit()

    assert status_of(sessions, current.id) == ("completed", None, True)


def test_failure_records_the_reason_and_finishes_the_job(
    sessions: sessionmaker[Session], collection_id: uuid.UUID
) -> None:
    queue_job(sessions, collection_id)
    with sessions() as session:
        claimed = claim_job(session, STALE_AFTER, MAX_ATTEMPTS)
    assert claimed is not None

    with sessions() as session:
        assert fail_job(session, claimed.id, claimed.attempts, "could not read pdf: broken")

    assert status_of(sessions, claimed.id) == ("failed", "could not read pdf: broken", True)


def test_stale_jobs_with_no_attempts_left_are_expired_as_failed(
    sessions: sessionmaker[Session], collection_id: uuid.UUID
) -> None:
    """No longer claimable, such a job would otherwise sit in running forever."""
    exhausted = queue_job(sessions, collection_id)
    retryable = queue_job(sessions, collection_id)
    with sessions() as session:
        for job_id, attempts in ((exhausted, MAX_ATTEMPTS), (retryable, 1)):
            session.execute(
                text("UPDATE ingestion_jobs SET status = 'running', attempts = :n WHERE id = :id"),
                {"n": attempts, "id": job_id},
            )
        session.commit()
    go_stale(sessions, exhausted)
    go_stale(sessions, retryable)

    with sessions() as session:
        assert expire_exhausted(session, STALE_AFTER, MAX_ATTEMPTS) == 1

    status, error, finished = status_of(sessions, exhausted)
    assert (status, finished) == ("failed", True)
    assert error is not None and f"{MAX_ATTEMPTS} attempts" in error
    assert status_of(sessions, retryable)[0] == "running"


def test_concurrent_workers_never_claim_the_same_job(
    sessions: sessionmaker[Session], collection_id: uuid.UUID
) -> None:
    """Workers released together drain one queue; every job goes to exactly one of them.

    The single-statement claim is what rules out a double claim, so this holds even
    without SKIP LOCKED: workers would queue behind each other instead. Losing SKIP
    LOCKED is caught by `test_a_claim_skips_a_row_another_worker_holds`.
    """
    queued = {queue_job(sessions, collection_id) for _ in range(CONCURRENT_JOBS)}
    start = threading.Barrier(CONCURRENT_WORKERS, timeout=10)

    def drain(_: int) -> list[uuid.UUID]:
        claimed: list[uuid.UUID] = []
        start.wait()
        with sessions() as session:
            while (job := claim_job(session, STALE_AFTER, MAX_ATTEMPTS)) is not None:
                claimed.append(job.id)
        return claimed

    with ThreadPoolExecutor(max_workers=CONCURRENT_WORKERS) as pool:
        per_worker = list(pool.map(drain, range(CONCURRENT_WORKERS)))

    claimed = [job_id for worker in per_worker for job_id in worker]
    assert len(claimed) == len(set(claimed)) == CONCURRENT_JOBS
    assert set(claimed) == queued
    # A race one worker won outright would say nothing about contention.
    assert sum(1 for worker in per_worker if worker) > 1

    # The database agrees: a job claimed twice would show two attempts.
    with sessions() as session:
        states = session.execute(
            text(
                "SELECT DISTINCT j.status, j.attempts FROM ingestion_jobs j "
                "JOIN documents d ON d.id = j.document_id WHERE d.collection_id = :collection"
            ),
            {"collection": collection_id},
        ).all()
    assert [tuple(state) for state in states] == [("running", 1)]


def test_a_claim_skips_a_row_another_worker_holds(
    sessions: sessionmaker[Session], collection_id: uuid.UUID
) -> None:
    """With the oldest job locked, a claim takes the next one rather than waiting.

    Without SKIP LOCKED the claim would queue behind the lock. The lock timeout turns
    that wait into an error, so a regression fails the test instead of hanging it.
    """
    held = queue_job(sessions, collection_id)
    free = queue_job(sessions, collection_id)

    with sessions() as locker:
        locker.execute(
            text("SELECT id FROM ingestion_jobs WHERE id = :id FOR UPDATE"), {"id": held}
        )
        with sessions() as session:
            session.execute(text("SET LOCAL lock_timeout = '2s'"))
            claimed = claim_job(session, STALE_AFTER, MAX_ATTEMPTS)
        locker.rollback()

    assert claimed is not None and claimed.id == free
    assert status_of(sessions, held)[0] == "queued"
