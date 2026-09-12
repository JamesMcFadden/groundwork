"""The frozen evaluation corpus, and the check that it is still what was frozen.

Runs are comparable only over identical documents, so every run hashes each file against
the manifest first and refuses to continue on any difference. The manifest is `shasum`
output, so `shasum -a 256 -c manifest.sha256` checks the same thing by hand.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

CORPUS_DIR = Path(__file__).parent / "corpus"
MANIFEST_NAME = "manifest.sha256"

# A 64-digit hex digest, a space, then a second space or the `*` of binary mode, then
# the filename. Binary mode changes nothing for SHA-256.
_MANIFEST_LINE = re.compile(r"^([0-9a-f]{64}) [ *](\S.*)$")


class CorpusError(Exception):
    """The corpus on disk is not the corpus the manifest froze."""


@dataclass(frozen=True, slots=True)
class CorpusDocument:
    filename: str
    path: Path
    sha256: str


@dataclass(frozen=True, slots=True)
class Corpus:
    """The verified documents in manifest order, and the manifest's own digest.

    The manifest digest identifies the corpus as a whole, so results can record exactly
    which documents they were measured on.
    """

    documents: tuple[CorpusDocument, ...]
    manifest_sha256: str


def parse_manifest(text: str) -> dict[str, str]:
    """Map each filename in a `shasum -a 256` manifest to its digest, in manifest order."""
    entries: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        match = _MANIFEST_LINE.match(line)
        if match is None:
            raise CorpusError(f"manifest line {number} is not '<sha256>  <filename>': {line!r}")
        digest, filename = match.groups()
        # Documents sit beside the manifest. A path could reach outside the corpus.
        if "/" in filename or "\\" in filename:
            raise CorpusError(f"manifest line {number} names a path, not a file: {filename}")
        if filename in entries:
            raise CorpusError(f"manifest lists {filename} twice")
        entries[filename] = digest
    if not entries:
        raise CorpusError("manifest lists no documents")
    return entries


def load_corpus(directory: Path = CORPUS_DIR) -> Corpus:
    """Check every document against the manifest, and return the verified corpus.

    Raises `CorpusError` if the manifest is missing or malformed, a PDF in the directory
    is not listed, a listed file is missing, or a file's digest has changed.
    """
    manifest_path = directory / MANIFEST_NAME
    try:
        manifest = manifest_path.read_bytes()
    except FileNotFoundError:
        raise CorpusError(f"no manifest at {manifest_path}") from None
    entries = parse_manifest(manifest.decode("utf-8"))

    # An unlisted document would never be ingested, so a run would silently measure a
    # different corpus from the one someone meant to add to.
    unlisted = sorted(path.name for path in directory.glob("*.pdf") if path.name not in entries)
    if unlisted:
        raise CorpusError(f"not in the manifest: {', '.join(unlisted)}")

    documents: list[CorpusDocument] = []
    for filename, expected in entries.items():
        path = directory / filename
        if not path.is_file():
            raise CorpusError(f"{filename} is in the manifest but missing")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise CorpusError(f"{filename} has changed: sha256 {actual}, manifest {expected}")
        documents.append(CorpusDocument(filename=filename, path=path, sha256=actual))

    return Corpus(documents=tuple(documents), manifest_sha256=hashlib.sha256(manifest).hexdigest())
