import hashlib

from app.services.storage import content_key


def test_content_key_is_the_sha256_of_the_bytes() -> None:
    data = b"the quick brown fox"

    assert content_key(data) == f"documents/{hashlib.sha256(data).hexdigest()}"


def test_identical_bytes_produce_one_key() -> None:
    """Re-uploading the same file must not create a second object."""
    assert content_key(b"same") == content_key(b"same")
    assert content_key(b"same") != content_key(b"different")
