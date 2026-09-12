"""The corpus check against real files. It reads only the filesystem, so it never skips."""

import hashlib
from pathlib import Path

import pytest

from eval.corpus import CORPUS_DIR, MANIFEST_NAME, CorpusError, load_corpus

COMMITTED = [
    "faint-lateral-cutoff.pdf",
    "iss-crew-workload-fatigue.pdf",
    "small-satellite-failure-rates.pdf",
    "uam-motion-sickness.pdf",
    "uam-risk.pdf",
    "x59-cumulative-noise-metrics.pdf",
]


@pytest.fixture
def corpus_dir(tmp_path: Path) -> Path:
    """A two-document corpus with a manifest that matches it."""
    lines = []
    for name, content in [("first.pdf", b"%PDF first"), ("second.pdf", b"%PDF second")]:
        (tmp_path / name).write_bytes(content)
        lines.append(f"{hashlib.sha256(content).hexdigest()}  {name}")
    (tmp_path / MANIFEST_NAME).write_text("\n".join(lines) + "\n")
    return tmp_path


def test_the_committed_corpus_matches_its_manifest() -> None:
    corpus = load_corpus()

    assert sorted(document.filename for document in corpus.documents) == COMMITTED
    manifest = (CORPUS_DIR / MANIFEST_NAME).read_bytes()
    assert corpus.manifest_sha256 == hashlib.sha256(manifest).hexdigest()


def test_a_matching_corpus_is_returned_in_manifest_order(corpus_dir: Path) -> None:
    corpus = load_corpus(corpus_dir)

    assert [document.filename for document in corpus.documents] == ["first.pdf", "second.pdf"]
    assert corpus.documents[0].sha256 == hashlib.sha256(b"%PDF first").hexdigest()


def test_a_changed_document_is_refused(corpus_dir: Path) -> None:
    """Even one byte: runs over different text are not comparable."""
    with (corpus_dir / "second.pdf").open("ab") as document:
        document.write(b" ")

    with pytest.raises(CorpusError, match="second.pdf has changed"):
        load_corpus(corpus_dir)


def test_a_missing_document_is_refused(corpus_dir: Path) -> None:
    (corpus_dir / "first.pdf").unlink()

    with pytest.raises(CorpusError, match="first.pdf is in the manifest but missing"):
        load_corpus(corpus_dir)


def test_an_unlisted_document_is_refused(corpus_dir: Path) -> None:
    (corpus_dir / "extra.pdf").write_bytes(b"%PDF extra")

    with pytest.raises(CorpusError, match="not in the manifest: extra.pdf"):
        load_corpus(corpus_dir)


def test_a_missing_manifest_is_refused(corpus_dir: Path) -> None:
    (corpus_dir / MANIFEST_NAME).unlink()

    with pytest.raises(CorpusError, match="no manifest"):
        load_corpus(corpus_dir)
