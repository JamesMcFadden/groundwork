import math
import random

from app.config import get_settings
from app.db.models import EMBEDDING_DIM
from app.ingest.chunk import CHUNK_TOKENS, chunk_pages
from app.ingest.parse import Page
from app.services.embeddings import Embedder, FastEmbedder

MODEL_WINDOW = 512

# Long and rare words split into several WordPiece tokens, so window edges regularly land
# on a word's continuation: the case that miscounts if the chunker cuts mid-word.
VOCABULARY = (
    "internationalization pharmacokinetics counterrevolutionary unbelievably "
    "electroencephalography well-known PDFs, re-embedded; tokenization: the a of "
    "retrieval citation overlap boundary heartbeat Postgres pgvector ingestion."
).split()


def prose(words: int, seed: int = 0) -> str:
    rng = random.Random(seed)
    return " ".join(rng.choice(VOCABULARY) for _ in range(words))


def onnx_threads(embedder: FastEmbedder) -> tuple[int, int]:
    """The intra-op and inter-op thread counts the embedder's ONNX Runtime session was given.

    fastembed exposes no accessor for its session, so this reaches in as the tokenizer
    check in `FastEmbedder` does.
    """
    session = getattr(embedder._model.model, "model", None)
    assert session is not None, "fastembed no longer keeps its session where this looks"
    options = session.get_session_options()
    return int(options.intra_op_num_threads), int(options.inter_op_num_threads)


def test_vectors_match_the_schema_dimension_and_are_unit_length(embedder: Embedder) -> None:
    vectors = embedder.embed_passages(["retrieval with citations", "heartbeat reclaim"])

    assert len(vectors) == 2
    for vector in vectors:
        assert len(vector) == EMBEDDING_DIM
        assert math.isclose(math.sqrt(sum(x * x for x in vector)), 1.0, abs_tol=1e-3)


def test_query_vectors_match_the_schema_dimension_and_are_unit_length(embedder: Embedder) -> None:
    vector = embedder.embed_query("how is a stalled ingestion job reclaimed?")

    assert len(vector) == EMBEDDING_DIM
    assert math.isclose(math.sqrt(sum(x * x for x in vector)), 1.0, abs_tol=1e-3)


def test_a_query_embeds_exactly_as_the_same_passage(embedder: Embedder) -> None:
    """No instruction prefix: fastembed embeds bge-small-en-v1.5 queries as passages.

    Search compares query vectors with passage vectors, so an upgrade that started
    prefixing queries would shift every result without an error. If this fails, that
    happened, and evaluation runs from before and after it are no longer comparable.
    """
    text = "how is a stalled ingestion job reclaimed?"

    assert embedder.embed_query(text) == embedder.embed_passages([text])[0]


def test_the_same_passage_embeds_the_same_way_twice(embedder: Embedder) -> None:
    """Evaluation compares runs over time, which means nothing if embedding drifts."""
    first = embedder.embed_passages(["a stable passage"])
    second = embedder.embed_passages(["a stable passage"])

    assert first == second


def test_a_thread_count_reaches_onnx_runtime_and_unset_leaves_it_to_choose(
    embedder: Embedder,
) -> None:
    """ONNX Runtime sizes its pools from the machine's CPUs, which a container's CPU limit
    does not change: under a 1-CPU limit, its default threads took query embedding from
    about 7 ms to 104 ms at P50. Pods with a limit set a count; unset, zero leaves the
    choice to ONNX Runtime, as before."""
    assert isinstance(embedder, FastEmbedder)
    capped = FastEmbedder(cache_dir=get_settings().embedding_cache_dir, threads=1)

    assert onnx_threads(embedder) == (0, 0)
    assert onnx_threads(capped) == (1, 1)


def test_the_tokenizer_reports_every_token_not_the_first_512(embedder: Embedder) -> None:
    """fastembed's own tokenizer truncates at 512; chunked with it, the rest would vanish."""
    text = prose(2000)

    spans = embedder.tokenizer.spans(text)

    assert len(spans) > MODEL_WINDOW
    assert spans[-1][1] == len(text)


def test_spans_point_into_the_original_text(embedder: Embedder) -> None:
    """The model lowercases and splits words; offsets must still recover the source."""
    text = "Well-known PDFs"

    spans = embedder.tokenizer.spans(text)

    assert [text[start:end] for start, end in spans] == ["Well", "-", "known", "PDF", "s"]


def test_text_longer_than_the_window_still_embeds(embedder: Embedder) -> None:
    """Only the chunker's copy is untruncated; the model keeps truncation as a backstop."""
    vectors = embedder.embed_passages([prose(2000)])

    assert len(vectors[0]) == EMBEDDING_DIM


def test_every_chunk_re_tokenizes_to_its_own_count(embedder: Embedder) -> None:
    """The model re-tokenizes chunk text rather than reusing the chunker's window.

    A window cut partway through a word leaves a fragment that tokenizes differently;
    on real prose that pushed full chunks to 511 tokens, past the window once the two
    special tokens are added, and the model truncated them.
    """
    pages = [Page(number=n, text=prose(700, seed=n)) for n in range(1, 6)]

    chunks = chunk_pages(pages, embedder.tokenizer)

    assert len(chunks) > 1
    assert CHUNK_TOKENS + 2 <= MODEL_WINDOW
    for chunk in chunks:
        assert len(embedder.tokenizer.spans(chunk.text)) == chunk.token_count
        assert chunk.token_count <= CHUNK_TOKENS
