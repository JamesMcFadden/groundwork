"""The API key test apps are built with, and the header that presents it."""

from pydantic import SecretStr

from app.config import Settings

TEST_API_KEY = "test-api-key"
AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}


def with_api_key(settings: Settings) -> Settings:
    """`settings` with the test key, whatever the environment supplies."""
    return settings.model_copy(update={"api_key": SecretStr(TEST_API_KEY)})
