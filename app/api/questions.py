import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.answering import RecordedQuestion, answer_question
from app.db.models import Collection
from app.deps import get_current_user_id, get_embedder, get_generator, get_session
from app.generation.generator import Generator
from app.schemas import CitationRead, QuestionAnswer, QuestionAsk, StageTimings
from app.services.embeddings import Embedder

router = APIRouter(prefix="/questions", tags=["questions"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_502_BAD_GATEWAY: {
            "description": "No answer could be generated. The question is still recorded."
        }
    },
)
def ask_question(
    body: QuestionAsk,
    session: Annotated[Session, Depends(get_session)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    generator: Annotated[Generator, Depends(get_generator)],
) -> QuestionAnswer:
    """Answer a question from one collection's documents, citing the passages it rests on.

    201 covers insufficient evidence as well as an answer: finding that the documents do
    not answer a question is a successful response to it. A declined or failed
    generation is recorded first, then reported as a 502 that says nothing of the cause.
    """
    owned = session.scalar(
        select(Collection.id).where(
            Collection.id == body.collection_id, Collection.user_id == user_id
        )
    )
    if owned is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="collection not found")

    recorded = answer_question(
        session,
        embedder,
        generator,
        collection_id=body.collection_id,
        user_id=user_id,
        text=body.question,
    )
    if recorded.question.outcome in ("declined", "failed"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="answer generation failed"
        )
    return _response(recorded)


def _response(recorded: RecordedQuestion) -> QuestionAnswer:
    question = recorded.question
    return QuestionAnswer(
        id=question.id,
        collection_id=question.collection_id,
        question=question.question_text,
        outcome="answered" if question.outcome == "answered" else "insufficient_evidence",
        answer=question.answer_text,
        citations=[
            CitationRead(
                marker=citation.marker,
                chunk_id=citation.chunk.chunk_id,
                document_id=citation.chunk.document_id,
                filename=citation.chunk.filename,
                page_start=citation.chunk.page_start,
                page_end=citation.chunk.page_end,
                score=citation.chunk.score,
            )
            for citation in recorded.citations
        ],
        timings=StageTimings(
            embed_ms=question.embed_ms,
            search_ms=question.search_ms,
            prep_ms=question.prep_ms,
            llm_ms=question.llm_ms,
            total_ms=question.total_ms,
        ),
    )
