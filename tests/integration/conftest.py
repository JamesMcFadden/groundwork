import pytest

from app.config import get_settings
from app.services.embeddings import Embedder, FastEmbedder


@pytest.fixture(scope="session")
def embedder() -> Embedder:
    """The real embedding model, loaded once for the whole run."""
    try:
        return FastEmbedder(cache_dir=get_settings().embedding_cache_dir)
    except ValueError as exc:
        # fastembed's signal that the weights are neither cached nor downloadable. Any
        # other error, EmbeddingModelError included, is a real failure and must fail.
        if "Could not load model" not in str(exc):
            raise
        pytest.skip("embedding model unavailable; it downloads once on first use (~63 MB)")
