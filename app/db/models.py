import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Fixed by the embedding model (bge-small-en-v1.5). Changing models requires a
# migration and a full re-embed, which is what the reindex endpoint exists for.
EMBEDDING_DIM = 384

# The text search configuration chunks are indexed with. A query must name the same one,
# or its words are stemmed differently from the index's and fail to match.
TEXT_SEARCH_CONFIG = "english"


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class User(Base):
    """Owner of collections. Authentication itself arrives in a later milestone."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    created_at: Mapped[datetime] = _created_at()

    collections: Mapped[list["Collection"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Collection(Base):
    """A named group of documents. Questions are asked against one collection."""

    __tablename__ = "collections"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_collections_user_name"),
        Index("ix_collections_user_created", "user_id", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = _created_at()

    user: Mapped[User] = relationship(back_populates="collections")
    documents: Mapped[list["Document"]] = relationship(
        back_populates="collection", cascade="all, delete-orphan"
    )


class Document(Base):
    """An uploaded file. The original bytes live in object storage under `s3_key`."""

    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_collection_created", "collection_id", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # sha256 of the contents, so re-uploading identical bytes reuses the object.
    s3_key: Mapped[str] = mapped_column(String(500), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created_at()

    collection: Mapped[Collection] = relationship(back_populates="documents")
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    jobs: Mapped[list["IngestionJob"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Chunk(Base):
    """A passage of a document, with its embedding.

    `collection_id` is denormalised from `documents` on purpose: the tenant-filtered
    vector search must stay single-table, since joining defeats the ANN index.
    """

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_chunks_document_index"),
        Index("ix_chunks_collection", "collection_id"),
        # Approximate nearest-neighbour search. The operator class must match the
        # search's <#> ordering, or Postgres cannot use the index for it at all.
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_ip_ops"},
        ),
        # Full-text search. GIN holds each lexeme once, with the chunks that contain it.
        Index("ix_chunks_tsv_gin", "tsv", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    # Full-text lexemes, computed by the database from `text`. Generated rather than set by
    # the worker, so every path that writes chunks fills it, the eval harness's included.
    tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(f"to_tsvector('{TEXT_SEARCH_CONFIG}', text)", persisted=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = _created_at()

    document: Mapped[Document] = relationship(back_populates="chunks")


class IngestionJob(Base):
    """Unit of work for the ingestion worker.

    This table is also the queue: workers claim rows with FOR UPDATE SKIP LOCKED.
    `heartbeat_at` lets a crashed worker's job be reclaimed; `attempts` stops a
    document that always fails from looping forever.
    """

    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_ingestion_jobs_status",
        ),
        # Serves the claim and the expiry of abandoned jobs, which both look only at queued
        # and running rows. Finished jobs accumulate forever; this index holds only the few
        # still in flight. Postgres uses a partial index only when it can prove a query's
        # condition implies the index's, so a new in-flight status must be added here too.
        Index(
            "ix_ingestion_jobs_claimable",
            "created_at",
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()

    document: Mapped[Document] = relationship(back_populates="jobs")


class Question(Base):
    """A question and its answer.

    Answer fields live here rather than in a separate table: the relationship is
    strictly one-to-one, and per-stage timings make latency analysis a query.

    Every question gets a row, whatever happened to it, so evaluation rates always have
    every question in the denominator.
    """

    __tablename__ = "questions"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('answered', 'insufficient_evidence', 'declined', 'failed')",
            name="ck_questions_outcome",
        ),
        CheckConstraint("retriever IN ('dense', 'hybrid')", name="ck_questions_retriever"),
        Index("ix_questions_collection_created", "collection_id", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    # The search that retrieved this question's chunks. Their recorded scores are inner
    # products under dense search and fused ranks under hybrid, unreadable without it.
    retriever: Mapped[str] = mapped_column(String(20), nullable=False)
    answer_text: Mapped[str | None] = mapped_column(Text)
    # Distinct cited numbers that validation rejected. None where no answer was checked,
    # so a question that never reached validation is not counted as citing perfectly.
    invalid_citations: Mapped[int | None] = mapped_column(Integer)
    # A declined or failed question's exception class name. Never its message, which can
    # carry a provider's or this service's internal detail.
    error_class: Mapped[str | None] = mapped_column(String(100))

    embed_ms: Mapped[int | None] = mapped_column(Integer)
    search_ms: Mapped[int | None] = mapped_column(Integer)
    prep_ms: Mapped[int | None] = mapped_column(Integer)
    llm_ms: Mapped[int | None] = mapped_column(Integer)
    total_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = _created_at()

    retrieval_results: Mapped[list["RetrievalResult"]] = relationship(
        back_populates="question", cascade="all, delete-orphan"
    )


class RetrievalResult(Base):
    """One chunk retrieved for one question, with its rank and whether it was cited.

    Retaining this is what makes offline analysis of real traffic possible.
    """

    __tablename__ = "retrieval_results"
    __table_args__ = (
        UniqueConstraint("question_id", "chunk_id", name="uq_retrieval_question_chunk"),
        Index("ix_retrieval_question_rank", "question_id", "rank"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    chunk_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float] = mapped_column(nullable=False)
    cited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    question: Mapped[Question] = relationship(back_populates="retrieval_results")
