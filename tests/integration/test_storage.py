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


def test_ensure_bucket_creates_a_missing_bucket(storage: ObjectStorage) -> None:
    """What Compose and CI rely on for a fresh volume."""
    bucket = f"test-{uuid.uuid4().hex[:12]}"
    fresh = build_storage(get_settings().model_copy(update={"s3_bucket": bucket}))

    fresh.ensure_bucket()

    try:
        fresh.put("probe", b"ok", "text/plain")
        assert fresh.get("probe") == b"ok"
    finally:
        # Cleanup reaches past the wrapper, which deliberately offers no deletes.
        fresh._client.delete_object(Bucket=bucket, Key="probe")
        fresh._client.delete_bucket(Bucket=bucket)


def test_ensure_bucket_is_safe_to_run_again(storage: ObjectStorage) -> None:
    """Compose and CI run it on every start, usually against a bucket that exists."""
    storage.ensure_bucket()
    storage.ensure_bucket()

    assert storage.exists(f"documents/{uuid.uuid4()}") is False
