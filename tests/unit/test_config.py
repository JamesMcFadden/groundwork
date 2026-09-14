import pathlib

import pytest
from pydantic import ValidationError

from app.config import Settings

SECRET_VARS = ["POSTGRES_PASSWORD", "S3_ACCESS_KEY", "S3_SECRET_KEY"]


@pytest.mark.parametrize("missing", SECRET_VARS)
def test_missing_secret_fails_at_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, missing: str
) -> None:
    """A misconfigured deployment must fail loudly rather than fall back to a default."""
    monkeypatch.chdir(tmp_path)  # the local .env must not supply the value
    for name in SECRET_VARS:
        monkeypatch.setenv(name, "supplied")
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError) as error:
        Settings()  # type: ignore[call-arg]

    assert missing.lower() in str(error.value)


def test_secrets_are_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POSTGRES_PASSWORD", "from-env")
    monkeypatch.setenv("S3_ACCESS_KEY", "key-from-env")
    monkeypatch.setenv("S3_SECRET_KEY", "also-from-env")

    settings = Settings()  # type: ignore[call-arg]

    assert settings.postgres_password == "from-env"
    assert "from-env" in settings.database_url


@pytest.fixture
def secrets_set(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Required secrets supplied, and no local .env to supply anything else."""
    monkeypatch.chdir(tmp_path)
    for name in SECRET_VARS:
        monkeypatch.setenv(name, "supplied")


def test_s3_keys_are_left_to_the_credential_chain_without_an_endpoint(
    monkeypatch: pytest.MonkeyPatch, secrets_set: None
) -> None:
    """On AWS the endpoint is empty and a pod's role supplies credentials, so no key is needed."""
    monkeypatch.setenv("S3_ENDPOINT", "")
    monkeypatch.delenv("S3_ACCESS_KEY")
    monkeypatch.delenv("S3_SECRET_KEY")

    settings = Settings()  # type: ignore[call-arg]

    assert settings.s3_access_key is None
    assert settings.s3_secret_key is None


def test_one_s3_key_without_the_other_fails_at_startup(
    monkeypatch: pytest.MonkeyPatch, secrets_set: None
) -> None:
    """Half a key pair can never sign a request, with an endpoint or without."""
    monkeypatch.setenv("S3_ENDPOINT", "")
    monkeypatch.delenv("S3_SECRET_KEY")

    with pytest.raises(ValidationError) as error:
        Settings()  # type: ignore[call-arg]

    assert "s3_secret_key" in str(error.value)


def test_retriever_defaults_to_hybrid(monkeypatch: pytest.MonkeyPatch, secrets_set: None) -> None:
    """Adopted under the rule pre-registered in docs/roadmap.md; changing it needs a new result."""
    monkeypatch.delenv("RETRIEVER", raising=False)

    assert Settings().retriever == "hybrid"  # type: ignore[call-arg]


def test_retriever_can_be_set_to_dense(monkeypatch: pytest.MonkeyPatch, secrets_set: None) -> None:
    monkeypatch.setenv("RETRIEVER", "dense")

    assert Settings().retriever == "dense"  # type: ignore[call-arg]


def test_an_unknown_retriever_fails_at_startup(
    monkeypatch: pytest.MonkeyPatch, secrets_set: None
) -> None:
    """A misspelt strategy must stop the service, not quietly fall back to one."""
    monkeypatch.setenv("RETRIEVER", "sparse")

    with pytest.raises(ValidationError) as error:
        Settings()  # type: ignore[call-arg]

    assert "retriever" in str(error.value)


def test_embedding_threads_are_left_to_onnx_runtime_unless_set(
    monkeypatch: pytest.MonkeyPatch, secrets_set: None
) -> None:
    monkeypatch.delenv("EMBEDDING_THREADS", raising=False)
    assert Settings().embedding_threads is None  # type: ignore[call-arg]

    monkeypatch.setenv("EMBEDDING_THREADS", "2")
    assert Settings().embedding_threads == 2  # type: ignore[call-arg]


def test_zero_embedding_threads_fail_at_startup(
    monkeypatch: pytest.MonkeyPatch, secrets_set: None
) -> None:
    """Zero is ONNX Runtime's own "choose for me", which a pod with a CPU limit must not get."""
    monkeypatch.setenv("EMBEDDING_THREADS", "0")

    with pytest.raises(ValidationError) as error:
        Settings()  # type: ignore[call-arg]

    assert "embedding_threads" in str(error.value)


def test_a_zero_database_connect_timeout_fails_at_startup(
    monkeypatch: pytest.MonkeyPatch, secrets_set: None
) -> None:
    """psycopg reads zero as its own 130 seconds, the wait the setting exists to bound."""
    monkeypatch.setenv("DATABASE_CONNECT_TIMEOUT_SECONDS", "0")

    with pytest.raises(ValidationError) as error:
        Settings()  # type: ignore[call-arg]

    assert "database_connect_timeout_seconds" in str(error.value)
