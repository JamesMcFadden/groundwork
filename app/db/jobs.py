"""The `ingestion_jobs` queue: claiming work, holding it, and finishing it.

The jobs table is the queue (ADR 0001). Workers claim rows with
`FOR UPDATE SKIP LOCKED`, so concurrent workers step over rows their peers hold
rather than blocking on them, and no separate broker is involved.

`attempts` doubles as a fencing token. Every claim increments it, and every later write
a worker makes — heartbeat, completion, failure — requires the value it claimed with.
A worker that stalls past the staleness window and is reclaimed therefore finds its
writes refused rather than landing on top of the worker that now holds the job.
"""

import uuid
from datetime import timedelta

from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.db.models import IngestionJob

# ORM updates here return what they touch; there are no loaded objects to synchronise.
_NO_SYNC = {"synchronize_session": False}


def claim_job(session: Session, stale_after: timedelta, max_attempts: int) -> IngestionJob | None:
    """Claim the oldest available job, or return None when there is nothing to do.

    Available means queued, or running but abandoned — a worker that died holding a
    claim stops refreshing `heartbeat_at`, and the row becomes claimable again once it
    goes stale. `attempts` bounds that reclaim so a document that reliably kills its
    worker cannot cycle forever.

    Selecting and updating in one statement is what makes the claim atomic: a
    read-then-write would leave a window in which two workers both believe they won.
    """
    # Staleness is measured against the database clock, not the worker's. Workers on
    # different hosts disagree about the time; the rows they are competing over do not.
    stale_cutoff = func.now() - stale_after

    # Claim always sets heartbeat_at, so a running row always has one to compare.
    candidate = (
        select(IngestionJob.id)
        .where(
            IngestionJob.attempts < max_attempts,
            or_(
                IngestionJob.status == "queued",
                and_(IngestionJob.status == "running", IngestionJob.heartbeat_at < stale_cutoff),
            ),
        )
        .order_by(IngestionJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )

    statement = (
        update(IngestionJob)
        .where(IngestionJob.id == candidate)
        .values(
            status="running",
            attempts=IngestionJob.attempts + 1,
            heartbeat_at=func.now(),
            started_at=func.now(),
            # A reclaimed job carries the previous attempt's error; it is not this
            # attempt's outcome, so it does not survive the claim.
            error=None,
        )
        .returning(IngestionJob)
    )
    job = session.scalars(statement, execution_options=_NO_SYNC).one_or_none()

    # Committed here rather than by the caller, unlike the request path. The attempt
    # must be durably counted before the slow work begins, or a worker that dies
    # mid-job leaves no record that it tried and the attempt bound means nothing.
    session.commit()
    return job


def heartbeat(session: Session, job_id: uuid.UUID, attempt: int) -> bool:
    """Refresh a claim, returning whether this worker still holds it."""
    refreshed = session.scalar(
        update(IngestionJob)
        .where(*_held(job_id, attempt))
        .values(heartbeat_at=func.now())
        .returning(IngestionJob.id),
        execution_options=_NO_SYNC,
    )
    session.commit()
    return refreshed is not None


def complete_job(session: Session, job_id: uuid.UUID, attempt: int) -> bool:
    """Mark a held job completed, without committing. Return whether it was held.

    The caller writes the job's results in the same transaction and commits the two
    together, so a document is indexed fully or not at all. The update also locks the
    job row: a second worker finishing the same job waits here, then finds the claim
    gone, rather than interleaving its writes.
    """
    completed = session.scalar(
        update(IngestionJob)
        .where(*_held(job_id, attempt))
        .values(status="completed", finished_at=func.now())
        .returning(IngestionJob.id),
        execution_options=_NO_SYNC,
    )
    return completed is not None


def fail_job(session: Session, job_id: uuid.UUID, attempt: int, error: str) -> bool:
    """Record a held job's terminal failure. Return whether it was held."""
    failed = session.scalar(
        update(IngestionJob)
        .where(*_held(job_id, attempt))
        .values(status="failed", error=error, finished_at=func.now())
        .returning(IngestionJob.id),
        execution_options=_NO_SYNC,
    )
    session.commit()
    return failed is not None


def expire_exhausted(session: Session, stale_after: timedelta, max_attempts: int) -> int:
    """Fail abandoned jobs with no attempts left, returning how many.

    `claim_job` stops offering such a job, which would otherwise sit in `running`
    forever with nothing recorded to say why.
    """
    expired = session.scalars(
        update(IngestionJob)
        .where(
            IngestionJob.status == "running",
            IngestionJob.heartbeat_at < func.now() - stale_after,
            IngestionJob.attempts >= max_attempts,
        )
        .values(
            status="failed",
            error=f"abandoned: no worker finished it in {max_attempts} attempts",
            finished_at=func.now(),
        )
        .returning(IngestionJob.id),
        execution_options=_NO_SYNC,
    ).all()
    session.commit()
    return len(expired)


def _held(job_id: uuid.UUID, attempt: int) -> tuple[ColumnElement[bool], ...]:
    """A job is held while it is running under the attempt that claimed it."""
    return (
        IngestionJob.id == job_id,
        IngestionJob.status == "running",
        IngestionJob.attempts == attempt,
    )
