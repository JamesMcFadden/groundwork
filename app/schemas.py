import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


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


class JobRead(BaseModel):
    """An ingestion job's progress.

    `heartbeat_at` is left out on purpose: it is how workers coordinate, not something a
    caller can act on.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    status: Literal["queued", "running", "completed", "failed"]
    attempts: int
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class QuestionAsk(BaseModel):
    collection_id: uuid.UUID
    # Stripped first, so a question of nothing but whitespace is rejected as empty.
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]


class CitationRead(BaseModel):
    """What one `[n]` marker in an answer points at."""

    marker: int
    chunk_id: int
    document_id: uuid.UUID
    filename: str
    page_start: int
    page_end: int
    score: float


class StageTimings(BaseModel):
    """Milliseconds each stage took, null for a stage that did not run.

    `total_ms` runs from receiving the question to having its outcome, and so excludes
    recording the question itself.
    """

    embed_ms: int | None
    search_ms: int | None
    prep_ms: int | None
    llm_ms: int | None
    total_ms: int | None


class QuestionAnswer(BaseModel):
    """An answer, or the finding that the collection's documents cannot give one.

    For insufficient evidence, `answer` is null and `citations` is empty.
    """

    id: uuid.UUID
    collection_id: uuid.UUID
    question: str
    outcome: Literal["answered", "insufficient_evidence"]
    answer: str | None
    citations: list[CitationRead]
    timings: StageTimings
