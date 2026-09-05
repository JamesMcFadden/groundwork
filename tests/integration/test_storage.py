import uuid
from collections.abc import Iterator

import pytest

from app.config import get_settings
from app.services.storage import ObjectStorage, build_storage, content_key


@pytest.fixture
def storage() -> Iterator[ObjectStorage]:
    store = build_storage(get_settings())
    try:
        store.exists("probe")
    except Exception:
        pytest.skip("object storage unavailable; start it with `docker compose up -d minio`")
    yield store


def test_put_get_round_trip(storage: ObjectStorage) -> None:
    data = f"contents {uuid.uuid4()}".encode()
    key = content_key(data)

    storage.put(key, data, "application/pdf")

    assert storage.get(key) == data
    assert storage.exists(key) is True


def test_exists_is_false_for_a_missing_key(storage: ObjectStorage) -> None:
    assert storage.exists(f"documents/{uuid.uuid4()}") is False
