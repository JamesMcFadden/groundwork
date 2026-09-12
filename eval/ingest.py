"""Loading the corpus into a collection of its own, owned by a user nothing else touches.

Ingestion reuses the service's own parse, chunk, and embed steps, then writes documents
and chunks directly. Object storage and the job queue are skipped: neither changes what
retrieval can find, and without them a run needs only PostgreSQL and the embedding model.

The eval user is not the default user, so integration-test fixtures, which clear the
default user's collections, never delete eval data. Each ingest replaces the eval user's
previous collections, so the vector index holds the same eval chunks from run to run.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Chunk, Collection, Document, User
from app.ingest.chunk import TextChunk, chunk_pages
from app.ingest.parse import parse_pdf
from app.services.embeddings import Embedder
from app.services.storage import content_key
from eval.corpus import CorpusDocument

EVAL_USER_EMAIL = "eval@groundwork.invalid"
COLLECTION_NAME = "golden-corpus"


@dataclass(frozen=True, slots=True)
class IngestedCorpus:
    """Where the corpus was indexed, and what indexing it produced.

    `pages` holds each document's parsed page text, first page at index 0, so evidence
    can be checked against exactly the text that was chunked without parsing twice.
    """

    collection_id: uuid.UUID
    user_id: uuid.UUID
    chunk_counts: dict[str, int]
    pages: dict[str, list[str]]


@dataclass(frozen=True, slots=True)
class _Prepared:
    document: CorpusDocument
    data: bytes
    pages: list[str]
    chunks: list[TextChunk]
    vectors: list[list[float]]


def ingest_corpus(
    sessions: sessionmaker[Session],
    embedder: Embedder,
    documents: Sequence[CorpusDocument],
    email: str = EVAL_USER_EMAIL,
) -> IngestedCorpus:
    """Replace the user's collections with one fresh collection holding these documents.

    Parsing, chunking, and embedding happen before any database work, so no connection
    is held while the model runs. The replacement is one transaction: a run that fails
    leaves the previous collection in place. Deleting a collection cascades to its
    documents, chunks, and recorded questions, so export results before re-ingesting.
    """
    prepared = [_prepare(document, embedder) for document in documents]

    with sessions() as session:
        user_id = _ensure_user(session, email)
        session.execute(delete(Collection).where(Collection.user_id == user_id))
        collection = Collection(user_id=user_id, name=COLLECTION_NAME)
        session.add(collection)
        session.flush()
        for item in prepared:
            session.add(_document(item, collection.id))
        session.commit()
        collection_id = collection.id

    return IngestedCorpus(
        collection_id=collection_id,
        user_id=user_id,
        chunk_counts={item.document.filename: len(item.chunks) for item in prepared},
        pages={item.document.filename: item.pages for item in prepared},
    )


def _prepare(document: CorpusDocument, embedder: Embedder) -> _Prepared:
    data = document.path.read_bytes()
    pages = parse_pdf(data)
    chunks = chunk_pages(pages, embedder.tokenizer)
    return _Prepared(
        document=document,
        data=data,
        pages=[page.text for page in pages],
        chunks=chunks,
        vectors=embedder.embed_passages([chunk.text for chunk in chunks]),
    )


def _ensure_user(session: Session, email: str) -> uuid.UUID:
    user_id = session.scalar(select(User.id).where(User.email == email))
    if user_id is None:
        user = User(email=email)
        session.add(user)
        session.flush()
        user_id = user.id
    return user_id


def _document(item: _Prepared, collection_id: uuid.UUID) -> Document:
    document = Document(
        collection_id=collection_id,
        filename=item.document.filename,
        content_type="application/pdf",
        size_bytes=len(item.data),
        # The key an upload of these bytes would have, though no object is stored.
        s3_key=content_key(item.data),
        page_count=len(item.pages),
    )
    document.chunks = [
        Chunk(
            collection_id=collection_id,
            chunk_index=chunk.index,
            text=chunk.text,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            token_count=chunk.token_count,
            embedding=vector,
        )
        for chunk, vector in zip(item.chunks, item.vectors, strict=True)
    ]
    return document
