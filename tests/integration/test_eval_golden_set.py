"""The committed golden set checked against the committed corpus."""

from app.ingest.parse import parse_pdf
from app.services.embeddings import Embedder
from eval.corpus import load_corpus
from eval.golden import check_evidence, load_golden


def test_the_golden_set_matches_the_corpus(embedder: Embedder) -> None:
    """Every quote is on the page it names, and short enough to lie whole within a chunk.

    Pages come from the application's own parser and token counts from the embedding
    model's tokenizer, so this checks the evidence exactly as scoring will read it. If it
    fails, the questions or the corpus have changed: that needs a dated note in
    success-criteria.md, never an edit that only makes this pass.
    """
    corpus = load_corpus()
    golden = load_golden([document.filename for document in corpus.documents])
    pages = {
        document.filename: [page.text for page in parse_pdf(document.path.read_bytes())]
        for document in corpus.documents
    }

    check_evidence(golden, pages, embedder.tokenizer)

    assert len(golden.answerable) == 30
    assert len(golden.unanswerable) == 8
