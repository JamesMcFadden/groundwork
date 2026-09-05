import uuid
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, read from the environment or a local .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str = "groundwork"
    postgres_password: str = "change-me"
    postgres_db: str = "groundwork"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # Until authentication lands, every request is attributed to this seeded user.
    # The row is created by a data migration so foreign keys stay NOT NULL.
    default_user_id: uuid.UUID = uuid.UUID("00000000-0000-0000-0000-000000000001")

    s3_endpoint: str = "http://localhost:9000"
    s3_bucket: str = "groundwork-documents"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "change-me"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
