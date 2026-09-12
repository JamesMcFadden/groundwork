import pathlib

import pytest
from pydantic import ValidationError

from app.config import Settings

SECRET_VARS = ["POSTGRES_PASSWORD", "S3_SECRET_KEY"]


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
