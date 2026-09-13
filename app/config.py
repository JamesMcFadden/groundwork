import uuid
from datetime import timedelta
from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
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

    # The API key maps every request to this seeded user. The row is created by a data
    # migration so foreign keys stay NOT NULL.
    default_user_id: uuid.UUID = uuid.UUID("00000000-0000-0000-0000-000000000001")

    # The key clients send in X-API-Key. Required when the API starts, not here: the worker
    # and the eval harness serve no HTTP and have no use for it.
    api_key: SecretStr | None = None

    # Empty means "use AWS": boto3 then resolves the real S3 endpoint itself.
    s3_endpoint: str = "http://localhost:9000"
    s3_region: str = "us-east-1"

    # Uploads are held in memory while hashing, so the cap is deliberate.
    max_upload_bytes: int = 25 * 1024 * 1024
    s3_bucket: str = "groundwork-documents"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str

    # How long the worker waits before asking for work again. ADR 0001 accepts roughly
    # one second of pickup latency as the price of not running a broker.
    worker_poll_seconds: float = 1.0

    # A claimed job whose worker has not checked in within this window is treated as
    # abandoned and may be reclaimed. Five minutes, per ADR 0001.
    job_stale_after_seconds: int = 300

    # Bounds reclaim, so a document that reliably kills its worker cannot cycle forever.
    job_max_attempts: int = 3

    # Where the embedding model's weights live. Unset, fastembed uses a directory under
    # the system temp dir, which a container loses on restart and CI loses every run;
    # images and CI set it so the weights download once.
    embedding_cache_dir: str | None = None

    # What answers questions. The real model by default, so a deployment that forgets to
    # choose fails for want of a key rather than quietly serving the stub's fake answers.
    generator: Literal["anthropic", "stub"] = "anthropic"

    # What retrieves a question's chunks: dense search alone, or dense and full-text search
    # fused by rank. Hybrid since it met the rule pre-registered for it in docs/roadmap.md.
    retriever: Literal["dense", "hybrid"] = "hybrid"

    # Needed only when the generator is the real model, so its absence is reported when
    # the API builds its generator, not here: the worker has no use for it.
    anthropic_api_key: SecretStr | None = None

    # How long one model response may take before the question is recorded as failed.
    generation_timeout_seconds: float = 60.0

    @property
    def job_stale_after(self) -> timedelta:
        return timedelta(seconds=self.job_stale_after_seconds)

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
