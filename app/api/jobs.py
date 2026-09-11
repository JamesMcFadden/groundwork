import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Collection, Document, IngestionJob
from app.deps import get_current_user_id, get_session
from app.schemas import JobRead

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}")
def get_job(
    job_id: uuid.UUID,
    session: Annotated[Session, Depends(get_session)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> JobRead:
    """Report how an upload's ingestion is going.

    Scoped through the job's document to the caller's collections. Someone else's job is
    a 404, exactly like a job that does not exist, so the response never confirms that
    another user's job id is real.
    """
    job = session.scalar(
        select(IngestionJob)
        .join(Document, Document.id == IngestionJob.document_id)
        .join(Collection, Collection.id == Document.collection_id)
        .where(IngestionJob.id == job_id, Collection.user_id == user_id)
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return JobRead.model_validate(job)
