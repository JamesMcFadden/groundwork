"""Turning passages into vectors, and deciding what counts as a token.

The embedding model and its tokenizer are one decision. Chunks are sized in the model's
tokens, so whatever embeds a chunk must also be what counted it; `Embedder` carries both
so they cannot be paired wrongly.
"""

from collections.abc import Sequence
from typing import Protocol

from fastembed import TextEmbedding
from tokenizers import Tokenizer as HFTokenizer

from app.db.models import EMBEDDING_DIM
from app.ingest.chunk import Tokenizer

# Fixed alongside EMBEDDING_DIM: changing model means a migration and a full re-embed.
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"


class EmbeddingModelError(RuntimeError):
    """The model loaded, but not in the shape the schema and the chunker depend on."""


class Embedder(Protocol):
    @property
    def tokenizer(self) -> Tokenizer: ...

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]: ...


class ModelTokenizer:
    """The model's own tokenizer, reporting every token rather than the first 512.

    fastembed configures its tokenizer to truncate at the model's window. That is right
    for embedding and wrong for chunking: used as-is, a document would be chunked from
    its first 512 tokens and everything after dropped without a word. This works on a
    copy with truncation and padding off, leaving the model's own truncation in place as
    a backstop.
    """

    def __init__(self, source: HFTokenizer) -> None:
        self._tokenizer = HFTokenizer.from_str(source.to_str())
        self._tokenizer.no_truncation()
        self._tokenizer.no_padding()

    def spans(self, text: str) -> list[tuple[int, int]]:
        # [CLS] and [SEP] have no position in the text. The chunk budget of 510 already
        # leaves room for them inside the model's 512.
        return list(self._tokenizer.encode(text, add_special_tokens=False).offsets)


class FastEmbedder:
    """bge-small-en-v1.5 through fastembed: quantized ONNX on CPU, no torch."""

    def __init__(self, cache_dir: str | None = None) -> None:
        self._model = TextEmbedding(EMBEDDING_MODEL, cache_dir=cache_dir)
        if self._model.embedding_size != EMBEDDING_DIM:
            raise EmbeddingModelError(
                f"{EMBEDDING_MODEL} produces {self._model.embedding_size} dimensions; "
                f"chunks.embedding holds {EMBEDDING_DIM}"
            )
        # fastembed offers no public accessor for its tokenizer. Reaching in is the only
        # way to be sure chunks are counted exactly as the model counts them, so an
        # upgrade that moves it fails here, by name, rather than miscounting quietly.
        source = getattr(self._model.model, "tokenizer", None)
        if not isinstance(source, HFTokenizer):
            raise EmbeddingModelError("fastembed no longer exposes its tokenizer")
        self._tokenizer = ModelTokenizer(source)

    @property
    def tokenizer(self) -> ModelTokenizer:
        return self._tokenizer

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        # Vectors come back unit-length, so cosine similarity and inner product rank alike.
        return [vector.tolist() for vector in self._model.passage_embed(list(texts))]
