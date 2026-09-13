from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings


def build_engine(settings: Settings) -> Engine:
    """Create the database engine.

    `pool_pre_ping` issues a cheap liveness check before handing out a pooled
    connection, so connections dropped by a restarted database surface as a retry
    rather than an error on the next request.

    `connect_timeout` bounds every attempt to connect. psycopg's own bound is 130 seconds:
    with PostgreSQL stopped in the kind cluster, a worker's poll hung that long before
    failing, and a request would wait as long before its 503.
    """
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        future=True,
        connect_args={"connect_timeout": settings.database_connect_timeout_seconds},
    )


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def database_ok(engine: Engine) -> bool:
    """Return whether the database answers a trivial query."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        return False
    return True
