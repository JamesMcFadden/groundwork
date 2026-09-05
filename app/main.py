from fastapi import FastAPI

from app import api
from app.config import Settings, get_settings
from app.db.session import build_engine, build_session_factory
from app.services.storage import build_storage


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application.

    Constructing the app in a function rather than at module scope keeps imports free of
    side effects and lets tests build an app with their own settings.
    """
    settings = settings or get_settings()
    app = FastAPI(
        title="Groundwork",
        description="RAG knowledge service with source-grounded, cited answers.",
        version="0.1.0",
    )
    engine = build_engine(settings)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine)
    app.state.storage = build_storage(settings)
    for router in api.routers:
        app.include_router(router)
    return app
