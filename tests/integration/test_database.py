import pytest

from app.config import get_settings
from app.db.session import build_engine, database_ok


@pytest.fixture(scope="module")
def engine():  # type: ignore[no-untyped-def]
    engine = build_engine(get_settings())
    if not database_ok(engine):
        pytest.skip("database unavailable; start it with `docker compose up -d postgres`")
    return engine


def test_database_answers_queries(engine) -> None:  # type: ignore[no-untyped-def]
    assert database_ok(engine) is True
