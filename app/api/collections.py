import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Collection
from app.deps import get_current_user_id, get_session
from app.pagination import decode_cursor, encode_cursor
from app.schemas import CollectionCreate, CollectionPage, CollectionRead

router = APIRouter(prefix="/collections", tags=["collections"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_collection(
    body: CollectionCreate,
    session: Annotated[Session, Depends(get_session)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> CollectionRead:
    collection = Collection(user_id=user_id, name=body.name)
    session.add(collection)
    try:
        session.commit()
    except IntegrityError as exc:
        # Relying on the unique constraint avoids a race between checking and inserting.
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"collection {body.name!r} already exists",
        ) from exc
    session.refresh(collection)
    return CollectionRead.model_validate(collection)


@router.get("")
def list_collections(
    session: Annotated[Session, Depends(get_session)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query()] = None,
) -> CollectionPage:
    statement = select(Collection).where(Collection.user_id == user_id)

    if cursor is not None:
        try:
            cursor_created_at, cursor_id = decode_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="malformed cursor"
            ) from exc
        # Row-value comparison: resumes after a specific row rather than skipping a
        # count, and is satisfied by ix_collections_user_created.
        statement = statement.where(
            tuple_(Collection.created_at, Collection.id) < (cursor_created_at, cursor_id)
        )

    # One extra row reveals whether a further page exists.
    rows = list(
        session.scalars(
            statement.order_by(Collection.created_at.desc(), Collection.id.desc()).limit(limit + 1)
        )
    )

    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = encode_cursor(items[-1].created_at, items[-1].id) if has_more else None
    return CollectionPage(
        items=[CollectionRead.model_validate(item) for item in items], next_cursor=next_cursor
    )
