import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class CollectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    created_at: datetime


class CollectionPage(BaseModel):
    items: list[CollectionRead]
    next_cursor: str | None = None


class DocumentAccepted(BaseModel):
    """Returned by upload: the work is queued, not done."""

    document_id: uuid.UUID
    job_id: uuid.UUID
    status: str
