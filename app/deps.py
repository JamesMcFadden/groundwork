import uuid
from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from app.config import Settings
from app.services.storage import ObjectStorage


def get_session(request: Request) -> Iterator[Session]:
    """Yield a session for the lifetime of one request.

    Routes commit explicitly; committing here would hide the transaction boundary.
    """
    with request.app.state.session_factory() as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise


def get_current_user_id(request: Request) -> uuid.UUID:
    """Resolve the acting user.

    Returns the seeded default user until authentication replaces this in M5.
    """
    user_id: uuid.UUID = request.app.state.settings.default_user_id
    return user_id


def get_storage(request: Request) -> ObjectStorage:
    storage: ObjectStorage = request.app.state.storage
    return storage


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings
