import hashlib
import pathlib

import pytest

from app.config import Settings
from app.services.storage import build_storage, content_key


def test_content_key_is_the_sha256_of_the_bytes() -> None:
    data = b"the quick brown fox"

    assert content_key(data) == f"documents/{hashlib.sha256(data).hexdigest()}"


def test_identical_bytes_produce_one_key() -> None:
    """Re-uploading the same file must not create a second object."""
    assert content_key(b"same") == content_key(b"same")
    assert content_key(b"same") != content_key(b"different")


@pytest.fixture
def credential_chain(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Credentials in the environment, the first link of boto3's default chain, and no others.

    No local .env, config file, cached login, or instance metadata can answer instead, so
    building a client does no I/O, and credentials it resolves can only have come from here.
    """
    monkeypatch.chdir(tmp_path)
    for name in (
        "AWS_PROFILE",
        "AWS_SESSION_TOKEN",
        "AWS_ROLE_ARN",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        "S3_ENDPOINT",
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.setenv("AWS_LOGIN_CACHE_DIRECTORY", str(tmp_path / "login"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "from-the-chain")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "chain-secret")


def test_without_keys_requests_are_signed_by_the_credential_chain(credential_chain: None) -> None:
    """On AWS a pod's role must sign requests, never a key the settings made up."""
    store = build_storage(Settings(postgres_password="unused", s3_endpoint=""))

    assert store._client._request_signer._credentials.access_key == "from-the-chain"


def test_keys_when_set_sign_requests_in_place_of_the_chain(credential_chain: None) -> None:
    store = build_storage(
        Settings(postgres_password="unused", s3_access_key="minio-user", s3_secret_key="secret")
    )

    assert store._client._request_signer._credentials.access_key == "minio-user"


def test_only_an_endpoint_gets_path_style_addressing(credential_chain: None) -> None:
    """MinIO needs bucket names in the path; S3 keeps its own default."""
    minio = build_storage(
        Settings(postgres_password="unused", s3_access_key="minio-user", s3_secret_key="secret")
    )
    aws = build_storage(Settings(postgres_password="unused", s3_endpoint=""))

    assert minio._client.meta.config.s3 == {"addressing_style": "path"}
    assert (aws._client.meta.config.s3 or {}).get("addressing_style") != "path"
    assert aws._client.meta.endpoint_url.endswith(".amazonaws.com")
