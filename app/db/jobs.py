"""Claiming work from the `ingestion_jobs` queue.

The jobs table is the queue (ADR 0001). Workers claim rows with
`FOR UPDATE SKIP LOCKED`, so concurrent workers step over rows their peers hold
rather than blocking on them, and no separate broker is involved.
"""

import uuid
from datetime import timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.db.models import IngestionJob


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
    job = session.scalars(statement, execution_options={"synchronize_session": False}).one_or_none()

    # Committed here rather than by the caller, unlike the request path. The attempt
    # must be durably counted before the slow work begins, or a worker that dies
    # mid-job leaves no record that it tried and the attempt bound means nothing.
    session.commit()
    return job


def heartbeat(session: Session, job_id: uuid.UUID) -> None:
    """Mark a claimed job as still being worked, so it is not reclaimed underneath us."""
    session.execute(
        update(IngestionJob)
        .where(IngestionJob.id == job_id, IngestionJob.status == "running")
        .values(heartbeat_at=func.now())
    )
    session.commit()
