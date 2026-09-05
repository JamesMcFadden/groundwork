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
