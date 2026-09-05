import uuid
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, read from the environment or a local .env file.

    Secrets are declared without defaults on purpose. A default would let a
    misconfigured deployment start with a known-weak credential instead of failing at
    startup with a message naming what is missing.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str = "groundwork"
    postgres_password: str
    postgres_db: str = "groundwork"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # Until authentication lands, every request is attributed to this seeded user.
    # The row is created by a data migration so foreign keys stay NOT NULL.
    default_user_id: uuid.UUID = uuid.UUID("00000000-0000-0000-0000-000000000001")

    s3_endpoint: str = "http://localhost:9000"
    s3_bucket: str = "groundwork-documents"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    # mypy cannot see that pydantic-settings populates required fields from the
    # environment, so it treats them as missing arguments.
    return Settings()  # type: ignore[call-arg]
