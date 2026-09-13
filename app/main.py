from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI

from app import api
from app.auth import configured_api_key, require_api_key
from app.config import Settings, get_settings
from app.db.session import build_engine, build_session_factory
from app.generation.factory import build_generator
from app.generation.generator import Generator
from app.logs import configure_logging
from app.middleware import RequestLogMiddleware
from app.services.embeddings import Embedder, FastEmbedder
from app.services.storage import build_storage


def create_app(
    settings: Settings | None = None,
    *,
    embedder: Embedder | None = None,
    generator: Generator | None = None,
) -> FastAPI:
    """Build the application.

    Constructing the app in a function rather than at module scope keeps imports free of
    side effects and lets tests build an app with their own settings, embedder, and
    generator.
    """
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Built at startup rather than for the first request, so a missing API_KEY, missing
        # weights, or a missing ANTHROPIC_API_KEY stop the process before it serves anything.
        app.state.api_key = configured_api_key(resolved)
        app.state.embedder = embedder or FastEmbedder(cache_dir=resolved.embedding_cache_dir)
        app.state.generator = generator or build_generator(resolved)
        yield

    app = FastAPI(
        title="Groundwork",
        description="RAG knowledge service with source-grounded, cited answers.",
        version="0.1.0",
        lifespan=lifespan,
    )
    engine = build_engine(resolved)
    app.state.settings = resolved
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine)
    app.state.storage = build_storage(resolved)
    for router in api.public_routers:
        app.include_router(router)
    # Attached here rather than route by route, so a route added to these cannot forget it.
    for router in api.protected_routers:
        app.include_router(router, dependencies=[Depends(require_api_key)])
    app.add_middleware(RequestLogMiddleware)
    return app


def main() -> None:
    """Serve the API, logging JSON lines as the worker does."""
    configure_logging()
    # log_config=None keeps the logging configured above instead of uvicorn's own, and the
    # middleware's request line replaces uvicorn's access log.
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host="0.0.0.0",
        port=8000,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
