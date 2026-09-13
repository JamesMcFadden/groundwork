"""Finding a caller's resources by id.

Every lookup filters on the caller's user id, so another user's resource comes back None
exactly as a missing one does, and routes answer both with the same 404: the response
never confirms that someone else's id is real. Search itself filters by collection alone,
since `chunks` carries no user id and a join would defeat its indexes; a route establishes
ownership here first.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Collection, Document, IngestionJob


def owned_collection(
    session: Session, collection_id: uuid.UUID, user_id: uuid.UUID
) -> Collection | None:
    """The collection, if it exists and belongs to the user."""
    return session.scalar(
        select(Collection).where(Collection.id == collection_id, Collection.user_id == user_id)
    )


def owned_job(session: Session, job_id: uuid.UUID, user_id: uuid.UUID) -> IngestionJob | None:
    """The job, if it exists and its document's collection belongs to the user."""
    return session.scalar(
        select(IngestionJob)
        .join(Document, Document.id == IngestionJob.document_id)
        .join(Collection, Collection.id == Document.collection_id)
        .where(IngestionJob.id == job_id, Collection.user_id == user_id)
    )
