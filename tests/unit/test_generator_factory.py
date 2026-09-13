import pathlib

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.generation.claude import ClaudeGenerator
from app.generation.factory import GeneratorConfigError, build_generator
from app.generation.stub import StubGenerator


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> Settings:
    """Settings from a clean environment: no local .env, and no generator configuration."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POSTGRES_PASSWORD", "unused")
    monkeypatch.setenv("S3_ACCESS_KEY", "unused")
    monkeypatch.setenv("S3_SECRET_KEY", "unused")
    for name in ("GENERATOR", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return Settings()  # type: ignore[call-arg]


def test_the_real_model_is_the_default(settings: Settings) -> None:
    assert settings.generator == "anthropic"


def test_the_stub_is_built_only_when_named(settings: Settings) -> None:
    generator = build_generator(settings.model_copy(update={"generator": "stub"}))

    assert isinstance(generator, StubGenerator)


@pytest.mark.parametrize("key", [None, SecretStr("")])
def test_the_real_model_without_a_key_refuses_to_build(
    settings: Settings, key: SecretStr | None
) -> None:
    """A missing key must stop the API at startup, never fall back to fake answers."""
    with pytest.raises(GeneratorConfigError, match="ANTHROPIC_API_KEY"):
        build_generator(settings.model_copy(update={"anthropic_api_key": key}))


def test_the_real_model_with_a_key_builds_the_claude_generator(settings: Settings) -> None:
    configured = settings.model_copy(update={"anthropic_api_key": SecretStr("sk-ant-test")})

    assert isinstance(build_generator(configured), ClaudeGenerator)


def test_the_key_is_read_from_the_environment_and_kept_out_of_reprs(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")

    loaded = Settings()  # type: ignore[call-arg]

    assert loaded.anthropic_api_key is not None
    assert loaded.anthropic_api_key.get_secret_value() == "sk-ant-from-env"
    assert "sk-ant-from-env" not in repr(loaded)
