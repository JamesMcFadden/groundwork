"""Stand-ins for the service's heavier collaborators, in tests that must never use them."""

from collections.abc import Sequence

from app.ingest.chunk import Tokenizer


class UnusedEmbedder:
    """Stands in for the embedding model, which no request in these tests may reach."""

    @property
    def tokenizer(self) -> Tokenizer:
        raise AssertionError("the embedder was used")

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        raise AssertionError("the embedder was used")

    def embed_query(self, text: str) -> list[float]:
        raise AssertionError("the embedder was used")
