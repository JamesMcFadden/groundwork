import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Document, IngestionJob
from app.db.ownership import owned_collection
from app.deps import get_current_user_id, get_session, get_settings_dep, get_storage
from app.schemas import DocumentAccepted
from app.services.storage import ObjectStorage, content_key

router = APIRouter(prefix="/documents", tags=["documents"])
logger = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"


def _read_within_limit(upload: UploadFile, limit: int) -> bytes:
    """Read the upload, rejecting anything over the configured size."""
    if upload.size is not None and upload.size > limit:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"file exceeds {limit} bytes",
        )
    data = upload.file.read()
    if len(data) > limit:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"file exceeds {limit} bytes",
        )
    return data


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def upload_document(
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[ObjectStorage, Depends(get_storage)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    collection_id: Annotated[uuid.UUID, Form()],
    file: Annotated[UploadFile, File()],
) -> DocumentAccepted:
    """Accept a document for asynchronous ingestion.

    Responds 202 rather than 201: the document exists, but its text is not yet
    searchable. Progress is followed through the returned job.
    """
    if owned_collection(session, collection_id, user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="collection not found")

    data = _read_within_limit(file, settings.max_upload_bytes)

    # Trust the bytes, not the client's declared content type.
    if not data.startswith(PDF_MAGIC):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="only PDF uploads are supported"
        )

    # Upload before the transaction. A stored object with no row is harmless — the key
    # is the content hash, so a later upload reuses it. A row pointing at a missing
    # object would not be recoverable.
    key = content_key(data)
    storage.put(key, data, "application/pdf")

    document = Document(
        collection_id=collection_id,
        filename=file.filename or "upload.pdf",
        content_type="application/pdf",
        size_bytes=len(data),
        s3_key=key,
    )
    job = IngestionJob(document=document, status="queued")
    session.add_all([document, job])
    # One transaction: a document is never left without queued work, which is what
    # removes the dual-write problem a separate queue would introduce.
    session.commit()
    session.refresh(document)
    session.refresh(job)
    # Logged with the request id, so a request leads to the worker's lines about its job.
    logger.info("queued ingestion job", extra={"document_id": document.id, "job_id": job.id})

    return DocumentAccepted(document_id=document.id, job_id=job.id, status=job.status)
